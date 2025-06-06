"""
Comprehensive tests for the EventBus implementation.

Tests cover:
- Basic event enqueueing and processing
- Sync and async contexts
- Handler registration and execution
- FIFO ordering
- Parallel handler execution
- Error handling
- Write-ahead logging
- Serialization
- Batch operations
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import pytest
from pydantic import Field

from browser_use.agent.cloud_events import CreateAgentTaskEvent
from browser_use.eventbus import BaseEvent, EventBus


# Test event models - using proper Event subclasses
class UserActionEvent(BaseEvent):
	"""Test event model for user actions"""

	event_type: str = Field(default='UserActionEvent', frozen=True)
	action: str
	user_id: str
	metadata: dict[str, Any] = Field(default_factory=dict)


class SystemEventModel(BaseEvent):
	"""Test event model for system events"""

	event_type: str = Field(default='SystemEventModel', frozen=True)
	event_name: str
	severity: str = 'info'
	details: dict[str, Any] = Field(default_factory=dict)


class MockAgent:
	"""Mock agent for testing"""

	def __init__(self, name: str = 'TestAgent'):
		self.name = name
		self.events_received = []


@pytest.fixture
async def eventbus():
	"""Create an event bus for testing"""
	agent = MockAgent()
	bus = EventBus()
	yield bus
	await bus.stop()


@pytest.fixture
def mock_agent():
	"""Create a mock agent"""
	return MockAgent()


class TestEventBusBasics:
	"""Test basic EventBus functionality"""

	async def test_eventbus_initialization(self, mock_agent):
		"""Test that EventBus initializes correctly"""
		bus = EventBus()

		assert bus._is_running is False
		assert bus._runloop_task is None
		assert len(bus.event_history) == 0
		assert len(bus.handlers['*']) == 1  # Default logger

	async def test_auto_start_and_stop(self, mock_agent):
		"""Test auto-start functionality and stopping the event bus"""
		bus = EventBus()

		# Should not be running initially
		assert bus._is_running is False
		assert bus._runloop_task is None

		# Auto-start by emitting an event
		bus.emit(UserActionEvent(action='test', user_id='user123'))
		await bus.wait_until_idle()

		# Should be running after auto-start
		assert bus._is_running is True
		assert bus._runloop_task is not None

		# Stop the bus
		await bus.stop()
		assert bus._is_running is False

		# Stop again should be idempotent
		await bus.stop()
		assert bus._is_running is False


class TestEventEnqueueing:
	"""Test event enqueueing functionality"""

	async def test_emit(self, eventbus):
		"""Test event emission"""
		event = UserActionEvent(action='login', user_id='user123')

		# Emit event
		result = eventbus.emit(event)

		# Check result
		assert isinstance(result, UserActionEvent)
		assert result.event_type == 'UserActionEvent'
		assert result.action == 'login'
		assert result.user_id == 'user123'
		assert result.event_id is not None
		assert result.queued_at is not None

		# Wait for processing
		await eventbus.wait_until_idle()

		# Check event history
		assert len(eventbus.event_history) == 1
		# The returned result should have completion information after waiting
		assert result.completed_at is not None
		assert result.started_at is not None

	def test_emit_sync(self, mock_agent):
		"""Test sync event emission"""
		bus = EventBus()
		event = SystemEventModel(event_name='startup', severity='info')

		# Emit event from sync context
		result = bus.emit(event)

		# Check result
		assert isinstance(result, SystemEventModel)
		assert result.event_type == 'SystemEventModel'
		assert result.event_name == 'startup'
		assert result.severity == 'info'

		# Check write-ahead log
		assert len(bus.event_history) == 1

	async def test_event_result(self, eventbus):
		"""Test event.result() pattern"""
		event = UserActionEvent(action='logout', user_id='user123')

		# Emit returns immediately
		emitted_event = eventbus.emit(event)
		assert emitted_event.queued_at is not None
		assert emitted_event.started_at is None  # Not started yet
		assert emitted_event.completed_at is None  # Not completed yet

		# Wait for completion
		result = await emitted_event.result()

		# Check that event was processed
		assert result.started_at is not None
		assert result.completed_at is not None
		assert result.results['_default_log_handler'] == 'logged'

	async def test_emit_convenience_method(self, eventbus):
		"""Test the emit() convenience method"""
		event = UserActionEvent(action='click', user_id='user123')

		# Use emit() method
		result = eventbus.emit(event)

		assert isinstance(result, BaseEvent)
		assert result.event_type == 'UserActionEvent'

		# Wait for processing
		await eventbus.wait_until_idle()


class TestHandlerRegistration:
	"""Test handler registration and execution"""

	async def test_subscribe_handler(self, eventbus):
		"""Test subscribing a handler to specific event type"""
		results = []

		async def user_action_handler(event: UserActionEvent) -> str:
			results.append(f'Handled {event.action}')
			return f'Processed {event.action}'

		# Subscribe handler
		eventbus.on('UserActionEvent', user_action_handler)

		# Emit event
		event = UserActionEvent(action='login', user_id='user123')
		eventbus.emit(event)
		await eventbus.wait_until_idle()

		# Check handler was called
		assert len(results) == 1
		assert results[0] == 'Handled login'

	async def test_subscribe_by_model(self, eventbus):
		"""Test subscribing a handler using model class"""
		results = []

		async def system_handler(event: SystemEventModel) -> str:
			results.append(event.event_name)
			return 'handled'

		# Subscribe using model
		eventbus.on(SystemEventModel, system_handler)

		# Emit event
		event = SystemEventModel(event_name='config_loaded')
		eventbus.emit(event)
		await eventbus.wait_until_idle()

		# Check handler was called
		assert len(results) == 1
		assert results[0] == 'config_loaded'

	async def test_subscribe_to_all(self, eventbus):
		"""Test subscribing a handler to all events"""
		all_events = []

		async def universal_handler(event: BaseEvent) -> str:
			all_events.append(event.event_type)
			return 'universal'

		# Subscribe to all
		eventbus.on('*', universal_handler)

		# Emit different event types
		eventbus.emit(UserActionEvent(action='login', user_id='u1'))
		eventbus.emit(SystemEventModel(event_name='startup'))
		await eventbus.wait_until_idle()

		# Check both events were handled
		assert len(all_events) == 2
		assert 'UserActionEvent' in all_events
		assert 'SystemEventModel' in all_events

	async def test_multiple_handlers_parallel(self, eventbus):
		"""Test that multiple handlers run in parallel"""
		start_times = []
		end_times = []

		async def slow_handler_1(event: BaseEvent) -> str:
			start_times.append(('h1', time.time()))
			await asyncio.sleep(0.1)
			end_times.append(('h1', time.time()))
			return 'handler1'

		async def slow_handler_2(event: BaseEvent) -> str:
			start_times.append(('h2', time.time()))
			await asyncio.sleep(0.1)
			end_times.append(('h2', time.time()))
			return 'handler2'

		# Subscribe both handlers
		eventbus.on('UserActionEvent', slow_handler_1)
		eventbus.on('UserActionEvent', slow_handler_2)

		# Emit event and wait
		start = time.time()
		event = await eventbus.emit(UserActionEvent(action='test', user_id='u1')).result()
		duration = time.time() - start

		# Check handlers ran in parallel (should take ~0.1s, not 0.2s)
		assert duration < 0.15
		assert len(start_times) == 2
		assert len(end_times) == 2

		# Check results
		assert event.results['slow_handler_1'] == 'handler1'
		assert event.results['slow_handler_2'] == 'handler2'

	def test_handler_can_be_sync_or_async(self, mock_agent):
		"""Test that both sync and async handlers are accepted"""
		bus = EventBus()

		def sync_handler(event: BaseEvent) -> str:
			return 'sync'

		async def async_handler(event: BaseEvent) -> str:
			return 'async'

		# Both should work
		bus.on('TestEvent', sync_handler)
		bus.on('TestEvent', async_handler)

		# Check both were registered
		assert len(bus.handlers['TestEvent']) == 2


class TestFIFOOrdering:
	"""Test FIFO event processing"""

	async def test_fifo_processing(self, eventbus):
		"""Test that events are processed in FIFO order"""
		processed_order = []

		async def order_handler(event: UserActionEvent) -> int:
			# Extract order from the metadata
			order = event.metadata.get('order', 0)
			processed_order.append(order)
			return order

		eventbus.on('*', order_handler)

		# Enqueue multiple events rapidly
		events = []
		for i in range(10):
			event = UserActionEvent(action=f'action_{i}', user_id='u1', metadata={'order': i})
			events.append(eventbus.emit(event))

		# Wait for all to process
		await eventbus.wait_until_idle()

		# Check order
		assert processed_order == list(range(10))


class TestErrorHandling:
	"""Test error handling in handlers"""

	async def test_handler_error_captured(self, eventbus):
		"""Test that handler errors are captured in event"""

		async def failing_handler(event: BaseEvent) -> str:
			raise ValueError('Handler failed!')

		eventbus.on('UserActionEvent', failing_handler)

		# Emit event
		event = await eventbus.emit(UserActionEvent(action='fail', user_id='u1')).result()

		# Check error was captured
		assert 'failing_handler' in event.errors
		assert isinstance(event.errors['failing_handler'], str)
		assert 'Handler failed!' in event.errors['failing_handler']

	async def test_one_handler_failure_doesnt_stop_others(self, eventbus):
		"""Test that one handler failing doesn't prevent others from running"""
		results = []

		async def failing_handler(event: BaseEvent) -> str:
			raise RuntimeError('I fail!')

		async def working_handler(event: BaseEvent) -> str:
			results.append('I work!')
			return 'success'

		eventbus.on('UserActionEvent', failing_handler)
		eventbus.on('UserActionEvent', working_handler)

		# Emit event
		event = await eventbus.emit(UserActionEvent(action='test', user_id='u1')).result()

		# Check both handlers ran
		assert len(results) == 1
		assert results[0] == 'I work!'
		assert event.results['working_handler'] == 'success'
		assert 'failing_handler' in event.errors


class TestBatchOperations:
	"""Test batch event operations"""

	async def test_batch_emit_with_gather(self, eventbus):
		"""Test batch event emission with asyncio.gather"""
		events = [
			UserActionEvent(action='login', user_id='u1'),
			SystemEventModel(event_name='startup'),
			UserActionEvent(action='logout', user_id='u1'),
		]

		# Enqueue batch
		emitted_events = [eventbus.emit(event) for event in events]
		results = await asyncio.gather(*[event.result() for event in emitted_events])

		# Check all processed
		assert len(results) == 3
		for result in results:
			assert result.completed_at is not None
			assert '_default_log_handler' in result.results

	async def test_empty_batch(self, eventbus):
		"""Test empty batch handling"""
		empty_events = []
		emitted_events = [eventbus.emit(event) for event in empty_events]
		results = await asyncio.gather(*[event.result() for event in emitted_events])
		assert results == []


class TestWriteAheadLog:
	"""Test write-ahead logging functionality"""

	async def test_write_ahead_log_captures_all_events(self, eventbus):
		"""Test that all events are captured in write-ahead log"""
		# Emit several events
		events = []
		for i in range(5):
			event = UserActionEvent(action=f'action_{i}', user_id='u1')
			events.append(eventbus.emit(event))

		await eventbus.wait_until_idle()

		# Check write-ahead log
		log = eventbus.event_history.copy()
		assert len(log) == 5
		for i, event in enumerate(log):
			assert event.action == f'action_{i}'

		# Check event state properties
		completed = eventbus.events_completed
		pending = eventbus.events_queued
		processing = eventbus.events_started
		assert len(completed) + len(pending) + len(processing) == len(log)
		assert len(completed) == 5  # All events should be completed
		assert len(pending) == 0  # No events should be pending
		assert len(processing) == 0  # No events should be processing


class TestSerialization:
	"""Test event serialization functionality"""

	async def test_wal_functionality_replaces_serialize(self, eventbus, tmp_path):
		"""Test that WAL persistence replaces old serialize functionality"""
		# WAL persistence is now tested in TestWALPersistence
		# This test confirms the old serialize functionality is no longer needed
		assert hasattr(eventbus, '_default_wal_handler')
		assert not hasattr(eventbus, 'serialize_events_to_file')


class TestEventCompletion:
	"""Test event completion tracking"""

	async def test_wait_for_completion(self, eventbus):
		"""Test waiting for event completion"""
		completion_order = []

		async def slow_handler(event: BaseEvent) -> str:
			await asyncio.sleep(0.1)
			completion_order.append('handler_done')
			return 'done'

		eventbus.on('UserActionEvent', slow_handler)

		# Enqueue without waiting
		event = eventbus.emit(UserActionEvent(action='test', user_id='u1'))
		completion_order.append('enqueue_done')

		# Wait for completion
		await event.wait_for_completion()
		completion_order.append('wait_done')

		# Check order
		assert completion_order == ['enqueue_done', 'handler_done', 'wait_done']
		assert event.completed_at is not None

	def test_completion_event_not_in_async_context(self):
		"""Test that completion event is None when not in async context"""
		# This test must run in sync context
		import threading

		result = {}

		def sync_test():
			# Create event outside async context
			event = UserActionEvent(action='test', user_id='u1')
			result['has_completion_event'] = event._completion_event is not None

		thread = threading.Thread(target=sync_test)
		thread.start()
		thread.join()

		# In sync context, completion event should be None
		assert not result.get('has_completion_event', True)


class TestEdgeCases:
	"""Test edge cases and special scenarios"""

	async def test_stop_with_pending_events(self, mock_agent):
		"""Test stopping event bus with events still in queue"""
		bus = EventBus()

		# Add a slow handler
		async def slow_handler(event: BaseEvent) -> str:
			await asyncio.sleep(1)
			return 'done'

		bus.on('*', slow_handler)

		# Enqueue events but don't wait
		for i in range(5):
			bus.emit(UserActionEvent(action=f'action_{i}', user_id='u1'))

		# Stop immediately
		await bus.stop()

		# Bus should stop even with pending events
		assert not bus._is_running

	async def test_event_with_complex_data(self, eventbus):
		"""Test events with complex nested data"""
		complex_data = {
			'nested': {
				'list': [1, 2, {'inner': 'value'}],
				'datetime': datetime.utcnow(),
				'none': None,
			}
		}

		event = SystemEventModel(event_name='complex', details=complex_data)

		result = await eventbus.emit(event).result()

		# Check data preserved
		assert result.details['nested']['list'][2]['inner'] == 'value'

	async def test_concurrent_emit_calls(self, eventbus):
		"""Test multiple concurrent emit calls"""
		# Create many events concurrently
		tasks = []
		for i in range(100):
			event = UserActionEvent(action=f'concurrent_{i}', user_id='u1')
			# Emit returns the event synchronously, but we need to wait for completion
			emitted_event = eventbus.emit(event)
			tasks.append(emitted_event.wait_for_completion())

		# Wait for all events to complete
		await asyncio.gather(*tasks)

		# Wait for processing
		await eventbus.wait_until_idle()

		# Check all events in log
		log = eventbus.event_history.copy()
		assert len(log) == 100

	async def test_rapid_fire_events_maintain_order(self, eventbus):
		"""Test that rapidly emitted events maintain strict FIFO order"""
		collected_orders = []

		async def handler(event: UserActionEvent):
			# Extract order from metadata
			order = event.metadata.get('order', -1)
			collected_orders.append(order)
			return f'handled_{order}'

		eventbus.on('UserActionEvent', handler)

		# Emit 100 events as fast as possible
		num_events = 100
		for i in range(num_events):
			event = UserActionEvent(action=f'rapid_{i}', user_id='u1', metadata={'order': i})
			eventbus.emit(event)

		# Wait for all events to process
		await eventbus.wait_until_idle()

		# Verify exact FIFO order
		assert collected_orders == list(range(num_events)), f'Events processed out of order: {collected_orders[:10]}...'

	async def test_mixed_delay_handlers_maintain_order(self, eventbus):
		"""Test that events with different handler delays still maintain FIFO order"""
		collected_orders = []
		handler_start_times = []

		async def handler(event: UserActionEvent):
			order = event.metadata.get('order', -1)
			handler_start_times.append((order, asyncio.get_event_loop().time()))
			# Simulate varying processing times
			if order % 2 == 0:
				await asyncio.sleep(0.05)  # Even events take longer
			else:
				await asyncio.sleep(0.01)  # Odd events are quick
			collected_orders.append(order)
			return f'handled_{order}'

		eventbus.on('UserActionEvent', handler)

		# Emit events
		num_events = 20
		for i in range(num_events):
			event = UserActionEvent(action=f'mixed_{i}', user_id='u1', metadata={'order': i})
			eventbus.emit(event)

		# Wait for all events to process
		await eventbus.wait_until_idle()

		# Verify exact FIFO order despite different processing times
		assert collected_orders == list(range(num_events)), f'Events processed out of order: {collected_orders}'

		# Verify handler start times are in order (events are dequeued in FIFO order)
		for i in range(1, len(handler_start_times)):
			prev_order, prev_time = handler_start_times[i - 1]
			curr_order, curr_time = handler_start_times[i]
			assert curr_time >= prev_time, f'Event {curr_order} started before event {prev_order}'

	async def test_all_handlers_complete_before_next_event(self, eventbus):
		"""Test that all handlers for an event complete before the next event is processed"""
		event_processing_log = []

		async def handler1(event: UserActionEvent):
			order = event.metadata.get('order', -1)
			event_processing_log.append(f'h1_start_{order}')
			await asyncio.sleep(0.05)
			event_processing_log.append(f'h1_end_{order}')
			return 'handler1'

		async def handler2(event: UserActionEvent):
			order = event.metadata.get('order', -1)
			event_processing_log.append(f'h2_start_{order}')
			await asyncio.sleep(0.03)
			event_processing_log.append(f'h2_end_{order}')
			return 'handler2'

		async def handler3(event: UserActionEvent):
			order = event.metadata.get('order', -1)
			event_processing_log.append(f'h3_start_{order}')
			await asyncio.sleep(0.01)
			event_processing_log.append(f'h3_end_{order}')
			return 'handler3'

		# Register all handlers
		eventbus.on('UserActionEvent', handler1)
		eventbus.on('UserActionEvent', handler2)
		eventbus.on('UserActionEvent', handler3)

		# Emit 3 events
		for i in range(3):
			event = UserActionEvent(action=f'multi_{i}', user_id='u1', metadata={'order': i})
			eventbus.emit(event)

		# Wait for all events to process
		await eventbus.wait_until_idle()

		# Verify that all handlers for event 0 complete before any handler for event 1 starts
		event0_starts = [log for log in event_processing_log if log.endswith('_start_0')]
		event0_ends = [log for log in event_processing_log if log.endswith('_end_0')]
		event1_starts = [log for log in event_processing_log if log.endswith('_start_1')]

		# All starts for event 0 should happen before all starts for event 1
		event0_last_start_idx = max(event_processing_log.index(s) for s in event0_starts)
		event1_first_start_idx = min(event_processing_log.index(s) for s in event1_starts)

		# All ends for event 0 should happen before any start for event 1
		event0_last_end_idx = max(event_processing_log.index(e) for e in event0_ends)

		assert event0_last_start_idx < event1_first_start_idx, 'Event 1 handlers started before event 0 handlers'
		assert event0_last_end_idx < event1_first_start_idx, 'Event 1 started before event 0 completed'

	async def test_event_completion_order_matches_emission_order(self, eventbus):
		"""Test that event completion tracking maintains FIFO order"""
		completion_order = []

		async def handler(event: UserActionEvent):
			order = event.metadata.get('order', -1)
			# Variable delays to test completion tracking
			delay = 0.1 - (order * 0.01)  # Later events process faster
			await asyncio.sleep(delay)
			return f'handled_{order}'

		eventbus.on('UserActionEvent', handler)

		# Emit events and track their completion
		events = []
		for i in range(10):
			event = UserActionEvent(action=f'track_{i}', user_id='u1', metadata={'order': i})
			eventbus.emit(event)
			events.append(event)

		# Wait for each event to complete and track order
		async def wait_and_track(event, order):
			await event.wait_for_completion()
			completion_order.append(order)

		# Wait for all completions in parallel
		await asyncio.gather(*[wait_and_track(e, e.metadata['order']) for e in events])

		# Verify completion order matches emission order
		assert completion_order == list(range(10)), f'Events completed out of order: {completion_order}'

	async def test_concurrent_emit_from_multiple_tasks_maintains_order(self, eventbus):
		"""Test that concurrent emits from multiple tasks maintain order within each task"""
		collected_events = []

		async def handler(event: UserActionEvent):
			order = event.metadata.get('order', -1)
			task_id = event.metadata.get('task_id', 'unknown')
			collected_events.append((order, task_id))
			return 'handled'

		eventbus.on('UserActionEvent', handler)

		# Create multiple tasks that emit events concurrently
		async def emit_task(task_id: str, start: int, count: int):
			for i in range(count):
				event = UserActionEvent(
					action=f'task_{task_id}_{i}', user_id='u1', metadata={'order': start + i, 'task_id': task_id}
				)
				eventbus.emit(event)
				# Small delay to interleave with other tasks
				await asyncio.sleep(0.001)

		# Start 3 tasks concurrently
		await asyncio.gather(emit_task('task1', 0, 5), emit_task('task2', 100, 5), emit_task('task3', 200, 5))

		# Wait for processing
		await eventbus.wait_until_idle()

		# Verify that events from each task maintain their relative order
		task1_events = [(order, task) for order, task in collected_events if task == 'task1']
		task2_events = [(order, task) for order, task in collected_events if task == 'task2']
		task3_events = [(order, task) for order, task in collected_events if task == 'task3']

		# Check order within each task
		assert [e[0] for e in task1_events] == list(range(0, 5))
		assert [e[0] for e in task2_events] == list(range(100, 105))
		assert [e[0] for e in task3_events] == list(range(200, 205))


class TestEventTypeOverride:
	"""Test that Event subclasses properly override event_type"""

	async def test_event_subclass_type(self, eventbus):
		"""Test that event subclasses maintain their type"""
		from browser_use.agent.cloud_events import CreateAgentTaskEvent

		# Create a specific event type
		event = CreateAgentTaskEvent(
			user_id='test_user', agent_session_id='12345678-1234-5678-1234-567812345678', llm_model='test-model', task='test task'
		)

		# Enqueue it
		result = eventbus.emit(event)

		# Check type is preserved - should be class name
		assert result.event_type == 'CreateAgentTask'
		assert isinstance(result, BaseEvent)

	async def test_event_schema_auto_generation(self, eventbus):
		"""Test that event_schema is automatically set with the correct format"""
		from browser_use.agent.cloud_events import CreateAgentSessionEvent
		from browser_use.utils import get_browser_use_version

		# Get the expected version
		version = get_browser_use_version()

		# Test with BaseEvent
		base_event = BaseEvent(event_type='TestEvent')
		assert base_event.event_schema is not None
		expected_base_schema = f'browser_use.eventbus.models.BaseEvent@{version}'
		assert base_event.event_schema == expected_base_schema

		# Test with CreateAgentTaskEvent
		task_event = CreateAgentTaskEvent(
			user_id='test_user', agent_session_id='12345678-1234-5678-1234-567812345678', llm_model='test-model', task='test task'
		)
		expected_task_schema = f'browser_use.agent.cloud_events.CreateAgentTaskEvent@{version}'
		assert task_event.event_schema == expected_task_schema

		# Test with CreateAgentSessionEvent
		session_event = CreateAgentSessionEvent(
			user_id='test_user',
			browser_session_id='test_session',
			browser_session_live_url='http://example.com',
			browser_session_cdp_url='ws://example.com',
		)
		expected_session_schema = f'browser_use.agent.cloud_events.CreateAgentSessionEvent@{version}'
		assert session_event.event_schema == expected_session_schema

		# Test custom event from this test file
		user_event = UserActionEvent(action='login', user_id='user123')
		expected_user_schema = f'test_eventbus.UserActionEvent@{version}'
		assert user_event.event_schema == expected_user_schema

		# Emit and check schema is preserved
		result = eventbus.emit(task_event)
		assert result.event_schema == expected_task_schema


class TestWALPersistence:
	"""Test automatic WAL persistence functionality"""

	async def test_wal_persistence_handler(self, tmp_path):
		"""Test that events are automatically persisted to WAL file"""
		# Create event bus with WAL path
		wal_path = tmp_path / 'test_events.jsonl'
		bus = EventBus(name='TestBus', wal_path=wal_path)

		try:
			# Emit some events
			events = []
			for i in range(3):
				event = UserActionEvent(action=f'action_{i}', user_id=f'user_{i}')
				emitted_event = bus.emit(event)
				result = await emitted_event.result()
				events.append(result)

			# Wait for processing
			await bus.wait_until_idle()

			# Check WAL file exists
			assert wal_path.exists()

			# Read and verify JSONL content
			lines = wal_path.read_text().strip().split('\n')
			assert len(lines) == 3

			# Parse each line as JSON
			for i, line in enumerate(lines):
				data = json.loads(line)
				assert data['event_type'] == 'UserActionEvent'
				assert data['action'] == f'action_{i}'
				assert data['user_id'] == f'user_{i}'
				# Check that datetime fields are properly serialized
				assert isinstance(data['queued_at'], str)
				assert isinstance(data['completed_at'], str)
				# Should be parseable as ISO format
				datetime.fromisoformat(data['queued_at'])
				datetime.fromisoformat(data['completed_at'])

		finally:
			await bus.stop()

	async def test_wal_persistence_creates_parent_dir(self, tmp_path):
		"""Test that WAL persistence creates parent directories"""
		# Use a nested path that doesn't exist
		wal_path = tmp_path / 'nested' / 'dirs' / 'events.jsonl'
		assert not wal_path.parent.exists()

		# Create event bus
		bus = EventBus(name='TestBus', wal_path=wal_path)

		# Parent directory should be created
		assert wal_path.parent.exists()
		try:
			# Emit an event
			await bus.emit(UserActionEvent(action='test', user_id='u1')).result()

			# Wait for WAL persistence to complete
			await bus.wait_until_idle()

			# Check file was created
			assert wal_path.exists()
		finally:
			await bus.stop()

	async def test_wal_persistence_skips_incomplete_events(self, tmp_path):
		"""Test that WAL persistence only writes completed events"""
		wal_path = tmp_path / 'incomplete_events.jsonl'
		bus = EventBus(name='TestBus', wal_path=wal_path)

		try:
			# Add a slow handler that will delay completion
			async def slow_handler(event: BaseEvent) -> str:
				await asyncio.sleep(0.1)
				return 'slow'

			bus.on('UserActionEvent', slow_handler)

			# Emit event without waiting
			event = bus.emit(UserActionEvent(action='test', user_id='u1'))

			# Check file doesn't exist yet (event not completed)
			assert not wal_path.exists()

			# Wait for completion
			await event.wait_for_completion()
			await bus.wait_until_idle()

			# Now file should exist with completed event
			assert wal_path.exists()
			lines = wal_path.read_text().strip().split('\n')
			assert len(lines) == 1
			data = json.loads(lines[0])
			assert data['event_type'] == 'UserActionEvent'
			assert 'completed_at' in data

		finally:
			await bus.stop()


if __name__ == '__main__':
	pytest.main([__file__, '-v'])
