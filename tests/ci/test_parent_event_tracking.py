"""
Test parent event tracking functionality in EventBus.
"""

import asyncio

import pytest

from browser_use.eventbus import BaseEvent, EventBus


class ParentEvent(BaseEvent):
	"""Parent event that triggers child events"""
	event_type: str = 'ParentEvent'
	message: str


class ChildEvent(BaseEvent):
	"""Child event triggered by parent"""
	event_type: str = 'ChildEvent'
	data: str


class GrandchildEvent(BaseEvent):
	"""Grandchild event triggered by child"""
	event_type: str = 'GrandchildEvent'
	value: int


@pytest.fixture
async def eventbus():
	"""Create an event bus for testing"""
	bus = EventBus(name='TestBus')
	yield bus
	await bus.stop()


class TestParentEventTracking:
	"""Test automatic parent event ID tracking"""

	async def test_basic_parent_tracking(self, eventbus):
		"""Test that child events automatically get parent_event_id"""
		child_events = []
		
		async def parent_handler(event: ParentEvent) -> str:
			# Handler that dispatches a child event
			child = ChildEvent(data=f"child_of_{event.message}")
			eventbus.dispatch(child)
			child_events.append(child)
			return 'parent_handled'
		
		eventbus.on('ParentEvent', parent_handler)
		
		# Dispatch parent event
		parent = ParentEvent(message='test_parent')
		parent_result = eventbus.dispatch(parent)
		
		# Wait for processing
		await eventbus.wait_until_idle()
		
		# Verify parent processed
		assert await parent_result.get('parent_handler') == 'parent_handled'
		
		# Verify child has parent_event_id set
		assert len(child_events) == 1
		child = child_events[0]
		assert child.parent_event_id == parent.event_id

	async def test_multi_level_parent_tracking(self, eventbus):
		"""Test parent tracking across multiple levels"""
		events_by_level = {'parent': None, 'child': None, 'grandchild': None}
		
		async def parent_handler(event: ParentEvent) -> str:
			events_by_level['parent'] = event
			child = ChildEvent(data='child_data')
			eventbus.dispatch(child)
			return 'parent'
		
		async def child_handler(event: ChildEvent) -> str:
			events_by_level['child'] = event
			grandchild = GrandchildEvent(value=42)
			eventbus.dispatch(grandchild)
			return 'child'
		
		async def grandchild_handler(event: GrandchildEvent) -> str:
			events_by_level['grandchild'] = event
			return 'grandchild'
		
		# Register handlers
		eventbus.on('ParentEvent', parent_handler)
		eventbus.on('ChildEvent', child_handler)
		eventbus.on('GrandchildEvent', grandchild_handler)
		
		# Start the chain
		parent = ParentEvent(message='root')
		eventbus.dispatch(parent)
		
		# Wait for all processing
		await eventbus.wait_until_idle()
		
		# Verify the parent chain
		assert events_by_level['parent'].parent_event_id is None  # Root has no parent
		assert events_by_level['child'].parent_event_id == parent.event_id
		assert events_by_level['grandchild'].parent_event_id == events_by_level['child'].event_id

	async def test_multiple_children_same_parent(self, eventbus):
		"""Test multiple child events from same parent"""
		child_events = []
		
		async def parent_handler(event: ParentEvent) -> str:
			# Dispatch multiple children
			for i in range(3):
				child = ChildEvent(data=f"child_{i}")
				eventbus.dispatch(child)
				child_events.append(child)
			return 'spawned_children'
		
		eventbus.on('ParentEvent', parent_handler)
		
		# Dispatch parent
		parent = ParentEvent(message='multi_child_parent')
		eventbus.dispatch(parent)
		
		await eventbus.wait_until_idle()
		
		# All children should have same parent
		assert len(child_events) == 3
		for child in child_events:
			assert child.parent_event_id == parent.event_id

	async def test_parallel_handlers_parent_tracking(self, eventbus):
		"""Test parent tracking with parallel handlers"""
		events_from_handlers = {'h1': [], 'h2': []}
		
		async def handler1(event: ParentEvent) -> str:
			await asyncio.sleep(0.01)  # Simulate work
			child = ChildEvent(data='from_h1')
			eventbus.dispatch(child)
			events_from_handlers['h1'].append(child)
			return 'h1'
		
		async def handler2(event: ParentEvent) -> str:
			await asyncio.sleep(0.02)  # Different timing
			child = ChildEvent(data='from_h2')
			eventbus.dispatch(child)
			events_from_handlers['h2'].append(child)
			return 'h2'
		
		# Both handlers respond to same event
		eventbus.on('ParentEvent', handler1)
		eventbus.on('ParentEvent', handler2)
		
		# Dispatch parent
		parent = ParentEvent(message='parallel_test')
		eventbus.dispatch(parent)
		
		await eventbus.wait_until_idle()
		
		# Both children should have same parent despite parallel execution
		assert len(events_from_handlers['h1']) == 1
		assert len(events_from_handlers['h2']) == 1
		assert events_from_handlers['h1'][0].parent_event_id == parent.event_id
		assert events_from_handlers['h2'][0].parent_event_id == parent.event_id

	async def test_explicit_parent_not_overridden(self, eventbus):
		"""Test that explicitly set parent_event_id is not overridden"""
		captured_child = None
		
		async def parent_handler(event: ParentEvent) -> str:
			nonlocal captured_child
			# Create child with explicit parent_event_id
			child = ChildEvent(data='explicit', parent_event_id='explicit_parent_id')
			eventbus.dispatch(child)
			captured_child = child
			return 'dispatched'
		
		eventbus.on('ParentEvent', parent_handler)
		
		parent = ParentEvent(message='test')
		eventbus.dispatch(parent)
		
		await eventbus.wait_until_idle()
		
		# Explicit parent_event_id should be preserved
		assert captured_child is not None
		assert captured_child.parent_event_id == 'explicit_parent_id'
		assert captured_child.parent_event_id != parent.event_id

	async def test_cross_eventbus_parent_tracking(self):
		"""Test parent tracking across multiple EventBuses"""
		bus1 = EventBus(name='Bus1')
		bus2 = EventBus(name='Bus2')
		
		captured_events = []
		
		async def bus1_handler(event: ParentEvent) -> str:
			# Dispatch child to bus2
			child = ChildEvent(data='cross_bus_child')
			bus2.dispatch(child)
			captured_events.append(('bus1', event, child))
			return 'bus1_handled'
		
		async def bus2_handler(event: ChildEvent) -> str:
			captured_events.append(('bus2', event))
			return 'bus2_handled'
		
		bus1.on('ParentEvent', bus1_handler)
		bus2.on('ChildEvent', bus2_handler)
		
		try:
			# Dispatch parent to bus1
			parent = ParentEvent(message='cross_bus_test')
			bus1.dispatch(parent)
			
			await bus1.wait_until_idle()
			await bus2.wait_until_idle()
			
			# Verify parent tracking works across buses
			assert len(captured_events) == 2
			_, parent_event, child_event = captured_events[0]
			_, received_child = captured_events[1]
			
			assert child_event.parent_event_id == parent.event_id
			assert received_child.parent_event_id == parent.event_id
			
		finally:
			await bus1.stop()
			await bus2.stop()

	async def test_sync_handler_parent_tracking(self, eventbus):
		"""Test parent tracking works with sync handlers"""
		child_events = []
		
		def sync_parent_handler(event: ParentEvent) -> str:
			# Sync handler that dispatches child
			child = ChildEvent(data='from_sync')
			eventbus.dispatch(child)
			child_events.append(child)
			return 'sync_handled'
		
		eventbus.on('ParentEvent', sync_parent_handler)
		
		parent = ParentEvent(message='sync_test')
		eventbus.dispatch(parent)
		
		await eventbus.wait_until_idle()
		
		# Parent tracking should work even with sync handlers
		assert len(child_events) == 1
		assert child_events[0].parent_event_id == parent.event_id

	async def test_error_handler_parent_tracking(self, eventbus):
		"""Test parent tracking when handler errors occur"""
		child_events = []
		
		async def failing_handler(event: ParentEvent) -> str:
			# Dispatch child before failing
			child = ChildEvent(data='before_error')
			eventbus.dispatch(child)
			child_events.append(child)
			raise ValueError("Handler failed")
		
		async def success_handler(event: ParentEvent) -> str:
			# This should still run
			child = ChildEvent(data='after_error')
			eventbus.dispatch(child)
			child_events.append(child)
			return 'success'
		
		eventbus.on('ParentEvent', failing_handler)
		eventbus.on('ParentEvent', success_handler)
		
		parent = ParentEvent(message='error_test')
		eventbus.dispatch(parent)
		
		await eventbus.wait_until_idle()
		
		# Both children should have parent_event_id despite error
		assert len(child_events) == 2
		for child in child_events:
			assert child.parent_event_id == parent.event_id


if __name__ == '__main__':
	pytest.main([__file__, '-v'])