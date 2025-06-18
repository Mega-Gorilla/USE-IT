import asyncio
import inspect
import logging
import warnings
from collections import defaultdict
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic import BaseModel
from uuid_extensions import uuid7str

from browser_use.eventbus.models import BaseEvent, EventHandler, PythonIdentifierStr, PythonIdStr, UUIDStr
from browser_use.utils import _log_pretty_path

logger = logging.getLogger(__name__)


# Context variable to track the current event being processed (for setting event_parent_id from inside a child event)
_current_event_context: ContextVar[BaseEvent | None] = ContextVar('current_event', default=None)


class EventBus:
	"""
	Async event bus with write-ahead logging and guaranteed FIFO processing.

	Features:
	- Enqueue events synchronously, await their results using 'await Event()'
	- FIFP Write-ahead logging with UUIDs and timestamps,
	- Serial event processing, parallel handler execution per event
	"""

	# Class Attributes
	name: PythonIdentifierStr
	parallel_handlers: bool = False
	wal_path: Path | None = None
	handlers: dict[PythonIdStr, list[EventHandler]]  # collected by .on(<event_type>, <handler>)

	# Runtime State
	id: UUIDStr
	event_queue: asyncio.Queue[BaseEvent] | None
	event_history: dict[UUIDStr, BaseEvent]  # collected by .dispatch(<event>)

	_is_running: bool = False
	_runloop_task: asyncio.Task | None = None
	_runloop_lock: asyncio.Lock | None = None
	_on_idle: asyncio.Event | None = None

	def __init__(
		self, name: PythonIdentifierStr | None = None, wal_path: Path | str | None = None, parallel_handlers: bool = False
	):
		self.id = uuid7str()
		self.name = name or f'EventBus_{self.id[-8:]}'
		assert self.name.isidentifier(), f'EventBus name must be a unique identifier string, got: {self.name}'
		self.event_queue = None
		self.event_history = {}
		self.handlers = defaultdict(list)
		self.parallel_handlers = parallel_handlers
		self.wal_path = Path(wal_path) if wal_path else None
		self._runloop_lock = None
		self._on_idle = None

		# Instead of registering as normal event handlers,
		# these special handlers are just called manually at the end of _run_loop_step
		# self.on('*', self._default_log_handler)
		# self.on('*', self._default_wal_handler)

	def __del__(self):
		"""Auto-cleanup on garbage collection"""
		if self._is_running:
			self._is_running = False

		if self._runloop_task and not self._runloop_task.done():
			try:
				self._runloop_task.cancel()
			except RuntimeError:
				pass  # No event loop - that's fine

			# Suppress the warning by accessing internal asyncio attributes
			# This prevents "Task was destroyed but it is pending" warnings
			if hasattr(self._runloop_task, '_log_destroy_pending'):
				self._runloop_task._log_destroy_pending = False

			# Also try to get the exception to mark it as handled
			try:
				self._runloop_task.exception()
			except (asyncio.CancelledError, asyncio.InvalidStateError):
				pass  # Expected for cancelled or pending tasks
			except RuntimeError:
				pass  # No event loop - that's fine

	def __str__(self) -> str:
		icon = '🟢' if self._is_running else '🔴'
		return f'{self.name}{icon}(⏳ {len(self.events_pending or [])} | ▶️ {len(self.events_started or [])} | ✅ {len(self.events_completed or [])} ➡️ {len(self.handlers)} 👂)'

	def __repr__(self) -> str:
		return str(self)

	@property
	def events_pending(self) -> list[BaseEvent]:
		"""Get events that haven't started processing yet (does not include events that have not even finished dispatching yet in self.event_queue)"""
		return [
			event for event in self.event_history.values() if event.event_started_at is None and event.event_completed_at is None
		]

	@property
	def events_started(self) -> list[BaseEvent]:
		"""Get events currently being processed"""
		return [event for event in self.event_history.values() if event.event_started_at and not event.event_completed_at]

	@property
	def events_completed(self) -> list[BaseEvent]:
		"""Get events that have completed processing"""
		return [event for event in self.event_history.values() if event.event_completed_at is not None]

	def on(self, event_pattern: PythonIdentifierStr | Literal['*'] | type[BaseModel], handler: EventHandler) -> None:
		"""
		Subscribe to events matching a pattern, event type name, or event model class.
		Use event_pattern='*' to subscribe to all events. Handler can be sync or async function or method.

		Examples:
			eventbus.on('TaskStartedEvent', handler)  # Specific event type
			eventbus.on(TaskStartedEvent, handler)  # Event model class
			eventbus.on('*', handler)  # Subscribe to all events
			eventbus.on('*', other_eventbus.dispatch)  # Forward all events to another EventBus

		Note: When forwarding events between buses, all handler results are automatically
		flattened into the original event's results, so EventResults sees all handlers
		from all buses as a single flat collection.
		"""
		# Determine event key
		if event_pattern == '*':
			event_key = '*'
		elif isinstance(event_pattern, type) and issubclass(event_pattern, BaseModel):
			event_key = event_pattern.__name__
		else:
			event_key = str(event_pattern)

		# Check for duplicate handler names
		if hasattr(handler, '__self__'):
			handler_name = f'{handler.__self__}.{handler.__name__}()'
		else:
			handler_name = handler.__name__
		existing_names = [h.__name__ for h in self.handlers.get(event_key, [])]

		if handler_name in existing_names:
			warnings.warn(
				f"⚠️  Handler '{handler_name}' already registered for event '{event_key}'. "
				f'This may cause ambiguous results when using name-based access. '
				f'Consider using unique function names.',
				UserWarning,
				stacklevel=2,
			)

		# Register handler
		self.handlers[event_key].append(handler)
		logger.debug(f'👂 {self}.on({event_key}, {handler_name}) Registered event handler')

	def dispatch(self, event: BaseEvent) -> BaseEvent:
		"""
		Enqueue an event for processing and immediately return an Event(status='pending') version (synchronous).
		You can then await the Event(status='pending') object to block until its Event(status='completed') versionis ready,
		or you can interact with the unawaited Event(status='pending') before its handlers have finished.

		(The first EventBus.dispatch() call will auto-start a bus's async _run_loop() if it's not already running)

		>>> completed_event = await eventbus.dispatch(SomeEvent())
			# 1. enqueues the event synchronously
			# 2. returns an awaitable SomeEvent() with pending results in .event_results
			# 3. awaits the SomeEvent() which waits until all pending results are complete and returns the completed SomeEvent()

		>>> result_value = await eventbus.dispatch(SomeEvent()).event_result()
			# 1. enqueues the event synchronously
			# 2. returns a pending SomeEvent() with pending results in .event_results
			# 3. awaiting .event_result() waits until all pending results are complete, and returns the raw result value of the first one
		"""
		assert event.event_id, 'Missing event.event_id: UUIDStr = uuid7str()'
		assert event.event_created_at, 'Missing event.event_created_at: datetime = datetime.now(UTC)'
		assert event.event_type and event.event_type.isidentifier(), 'Missing event.event_type: str'
		assert event.event_schema and '@' in event.event_schema, 'Missing event.event_schema: str (with @version)'

		# Automatically set event_parent_id from context if not already set
		if event.event_parent_id is None:
			current_event = _current_event_context.get()
			if current_event is not None:
				event.event_parent_id = current_event.event_id

		# Add this EventBus to the event_path if not already there
		if self.name not in event.event_path:
			# preserve identity of the original object instead of creating a new one, so that the original object remains awaitable to get the result
			# NOT: event = event.model_copy(update={'event_path': event.event_path + [self.name]})
			event.event_path.append(self.name)
		else:
			logger.debug(
				f'⚠️ {self}.dispatch({event.event_type}) - Bus already in path, not adding again. Path: {event.event_path}'
			)

		assert event.event_path, 'Missing event.event_path: list[str] (with at least the origin function name recorded in it)'
		assert all(entry.isidentifier() for entry in event.event_path), (
			f'Event.event_path must be a list of valid EventBus names, got: {event.event_path}'
		)

		# Add event to history
		self.event_history[event.event_id] = event
		logger.debug(f'📝 {self}.dispatch() adding event {event.event_id} to history')

		# Auto-start if needed
		self._start()

		# Put event in queue synchronously using put_nowait
		if self.event_queue:
			try:
				self.event_queue.put_nowait(event)
				logger.debug(
					f'🗣️ {self}.dispatch({event.event_type}) ➡️ Event#{event.event_id[-8:]}({event.event_status} #{self.event_queue.qsize()})'
				)
			except asyncio.QueueFull:
				logger.error(
					f'⚠️ {self} Event queue is full! Dropping event and aborting {event.event_type}:\n{event.model_dump_json()}'
				)
				raise  # could also block indefinitely until queue has space, but dont drop silently or delete events
		else:
			logger.warning(f'⚠️ {self}.dispatch() called but event_queue is None! Event not queued: {event.event_type}')

		# Note: We do NOT pre-create EventResults here anymore.
		# EventResults are created only when handlers actually start executing.
		# This avoids "orphaned" pending results for handlers that get filtered out later.

		return event

	async def expect(
		self,
		event_type: PythonIdentifierStr | type[BaseModel],
		timeout: float | None = None,
		predicate: Callable[[BaseEvent], bool] | None = None,
	) -> BaseEvent:
		"""
		Wait for an event matching the given type/pattern with optional predicate filter.

		Args:
			event_type: The event type string or model class to wait for
			timeout: Maximum time to wait in seconds (None = wait forever)
			predicate: Optional filter function that must return True for the event to match

		Returns:
			The first matching event

		Raises:
			asyncio.TimeoutError: If timeout is reached before a matching event

		Example:
			# Wait for any response event
			response = await eventbus.expect('ResponseEvent', timeout=30)

			# Wait for specific response with predicate
			response = await eventbus.expect(
				'ResponseEvent',
				predicate=lambda e: e.request_id == my_request_id,
				timeout=30
			)
		"""
		future: asyncio.Future[BaseEvent] = asyncio.Future()

		def notify_expect_handler(event: BaseEvent) -> None:
			"""Handler that resolves the future when a matching event is found"""
			if not future.done() and (predicate is None or predicate(event)):
				future.set_result(event)

		notify_expect_handler.__name__ = f'{self}.expect({event_type}, predicate={predicate and id(predicate)})@{_log_pretty_path(inspect.currentframe().f_code.co_filename)}:{inspect.currentframe().f_lineno}'  # add file and line number to the name

		# Register temporary handler
		self.on(event_type, notify_expect_handler)

		try:
			# Wait for the future with optional timeout
			return await asyncio.wait_for(future, timeout=timeout)
		finally:
			# Clean up handler
			event_key = event_type.__name__ if isinstance(event_type, type) else str(event_type)
			if event_key in self.handlers and notify_expect_handler in self.handlers[event_key]:
				self.handlers[event_key].remove(notify_expect_handler)

	def _start(self) -> None:
		"""Start the event bus if not already running"""
		if not self._is_running:
			try:
				loop = asyncio.get_running_loop()
				# Create async objects if needed
				if self.event_queue is None:
					self.event_queue = asyncio.Queue()
					self._runloop_lock = asyncio.Lock()
					self._on_idle = asyncio.Event()
					self._on_idle.clear()  # Start in a busy state unless we confirm queue is empty by running _run_loop_step() at least once
				# Create and start the run loop task
				self._runloop_task = loop.create_task(self._run_loop(), name=f'{self}._run_loop')
				self._is_running = True
			except RuntimeError:
				# No event loop - will start when one becomes available
				pass

	async def stop(self, timeout: float = 0.0) -> None:
		"""Stop the event bus, optionally waiting for events to complete"""
		if not self._is_running:
			return

		# Wait for completion if timeout specified
		if timeout:
			try:
				await self.wait_until_idle(timeout=timeout)
			except TimeoutError:
				pass

		queue_size = self.event_queue.qsize() if self.event_queue else 0
		if queue_size or self.events_pending or self.events_started:
			logger.debug(
				f'⚠️ {self} stopping with pending events: Pending {len(self.events_pending) + queue_size} | Started {len(self.events_started)} | Completed {len(self.events_completed)}\n'
				f'PENDING={self.events_pending}\nSTARTED={self.events_started}'
			)

		# Signal shutdown
		self._is_running = False

		# Cancel the run loop task if it exists
		if self._runloop_task and not self._runloop_task.done():
			self._runloop_task.cancel()
			try:
				# Wait for task to finish cancellation
				await asyncio.wait_for(self._runloop_task, timeout=1.0)
			except (asyncio.CancelledError, TimeoutError):
				# Expected - task was cancelled or timed out
				pass
			except Exception as e:
				logger.debug(f'Exception while stopping {self}: {e}')

		# Clear references
		self._runloop_task = None
		self._runloop_lock = None
		if self._on_idle:
			self._on_idle.set()

		logger.debug(f'🛑 {self} shut down gracefully' if timeout is not None else f'🛑 {self} killed')

	async def wait_until_idle(self, timeout: float | None = None) -> None:
		"""Wait until the event bus is idle (no events being processed and all handlers completed)"""

		self._start()
		assert self._on_idle and self.event_queue, 'EventBus._start() must be called before wait_until_idle() is reached'

		# First wait for the queue to be empty
		# Then wait for idle state with timeout
		try:
			await asyncio.wait_for(self.event_queue.join(), timeout=timeout)
			await asyncio.wait_for(self._on_idle.wait(), timeout=timeout)
		except TimeoutError:
			logger.warning(
				f'⌛️ {self} Timeout waiting for event bus to be idle after {timeout}s (processing: {len(self.events_started)})'
			)

	async def _run_loop(self) -> None:
		"""Main event processing loop"""
		try:
			while self._is_running:
				try:
					_processed_event = await self._run_loop_step()
				except RuntimeError as e:
					# Event loop is closing
					if 'Event loop is closed' in str(e) or 'no running event loop' in str(e):
						break
					else:
						logger.exception(f'❌ {self} Runtime error in event loop: {e}')
						# Continue running even if there's an error
				except Exception as e:
					logger.exception(f'❌ {self} Error in event loop: {type(e).__name__} {e}')
					# Continue running even if there's an error
		except asyncio.CancelledError:
			# Task was cancelled, clean exit
			# logger.debug(f'🛑 {self} Event loop task cancelled')
			pass
		finally:
			await self.stop()

	async def _run_loop_step(
		self, event: BaseEvent | None = None, timeout: float | None = None, wait_for_timeout: float = 0.1
	) -> BaseEvent:
		"""Process a single event from the queue"""

		assert self._runloop_lock, 'EventBus._start() must be called before _run_loop_step()'
		assert self._on_idle, 'EventBus._start() must be called before _run_loop_step()'

		# Wait for next event with timeout to periodically check idle state
		if event is None:
			try:
				event = await asyncio.wait_for(self.event_queue.get(), timeout=wait_for_timeout)
			except TimeoutError:
				if not (self.events_pending or self.events_started or self.event_queue.qsize()):
					logger.debug(f'🛑 {self} idle')
					self._on_idle.set()
				return None

		logger.debug(f'🏃 {self}._run_loop_step({event}) STARTING')

		# Clear idle state when we get an event
		self._on_idle.clear()

		# Process the event
		async with self._runloop_lock:
			# Execute all handlers for this event
			applicable_handlers = self._get_applicable_handlers(event)
			await self._execute_handlers(event, handlers=applicable_handlers, timeout=timeout)

			await self._default_log_handler(event)
			await self._default_wal_handler(event)

			# Mark event as complete if all handlers are done
			event._mark_complete_if_all_handlers_completed()

			# Mark task as done
			self.event_queue.task_done()

		logger.debug(f'✅ {self}._run_loop_step({event}) COMPLETE')
		return event

	def _get_applicable_handlers(self, event: BaseEvent) -> dict[str, EventHandler]:
		"""Get all handlers that should process the given event, filtering out those that would create loops"""
		applicable_handlers = []

		# Add event-type-specific handlers
		applicable_handlers.extend(self.handlers.get(event.event_type, []))

		# Add wildcard handlers (handlers registered for '*')
		applicable_handlers.extend(self.handlers.get('*', []))

		# Filter out handlers that would create loops and build id->handler mapping
		# Use handler id as key to preserve all handlers even with duplicate names
		filtered_handlers = {}
		for handler in applicable_handlers:
			if self._would_create_loop(event, handler):
				logger.debug(f'⚠️ {self} Skipping {handler.__name__}#{str(id(handler))[-4:]}({event}) to prevent infinite loop')
				continue
			else:
				handler_id = str(id(handler))
				filtered_handlers[handler_id] = handler

		return filtered_handlers

	async def _execute_handlers(
		self, event: BaseEvent, handlers: dict[PythonIdStr, EventHandler] | None = None, timeout: float | None = None
	) -> None:
		"""Execute all handlers for an event in parallel"""
		applicable_handlers = handlers if (handlers is not None) else self._get_applicable_handlers(event)
		if not applicable_handlers:
			event._mark_complete_if_all_handlers_completed()  # mark event completed immediately if it has no handlers
			return

		# Execute all handlers in parallel
		if self.parallel_handlers:
			handler_tasks = {}
			for handler_id, handler in applicable_handlers.items():
				task = asyncio.create_task(
					self._execute_sync_or_async_handler(event, handler, timeout=timeout), name=f'{self}.{handler.__name__}'
				)
				handler_tasks[handler_id] = (task, handler)

			# Wait for all handlers to complete
			for handler_id, (task, handler) in handler_tasks.items():
				try:
					await task
				except Exception as e:
					# Error already logged and recorded in _execute_sync_or_async_handler
					pass
		else:
			# otherwise, execute handlers serially, wait until each one completes before moving on to the next
			for handler_id, handler in applicable_handlers.items():
				try:
					await self._execute_sync_or_async_handler(event, handler, timeout=timeout)
				except Exception as e:
					# Error already logged and recorded in _execute_sync_or_async_handler
					logger.debug(
						f'❌ {self} Handler {handler.__name__}#{str(id(handler))[-4:]}({event}) failed with {type(e).__name__}: {e}'
					)
					pass

	async def _execute_sync_or_async_handler(self, event: BaseEvent, handler: EventHandler, timeout: float | None = None) -> Any:
		"""Safely execute a single handler with deadlock detection"""

		logger.debug(f' ↳ {self}._execute_handler({event}, handler={handler.__name__}#{str(id(handler))[-4:]})')

		# Check if this handler has already been executed for this event
		handler_id = str(id(handler))
		if handler_id in event.event_results:
			existing_result = event.event_results[handler_id]
			if existing_result.started_at is not None:
				raise RuntimeError(
					f'Handler {handler.__name__}#{handler_id[-4:]} has already been executed for event {event.event_id}. '
					f'Previous execution started at {existing_result.started_at}'
				)

		# Mark handler as started
		event_result = event.event_result_update(
			handler=handler, eventbus=self, status='started', timeout=timeout or event.event_timeout
		)

		# Set the current event in context so child events can reference it
		token = _current_event_context.set(event)

		# Create a task to monitor for potential deadlock / slow handlers
		async def deadlock_monitor():
			await asyncio.sleep(15.0)
			logger.warning(
				f'⚠️ {self}.{handler.__name__}() has been running for >15s on event. Possible slow processing or deadlock.\n'
				'(handler could be trying to await its own result or another blocked async task).\n'
				f'{self}.{handler.__name__}({event})'
			)

		monitor_task = asyncio.create_task(deadlock_monitor(), name=f'{self}.deadlock_monitor.{handler.__name__}')

		try:
			if inspect.iscoroutinefunction(handler):
				result = await asyncio.wait_for(handler(event), timeout=event_result.timeout)
			else:
				# Run sync handler directly in the main thread
				# This blocks but ensures we have access to the event loop
				result = handler(event)
				logger.debug(f'    ↳ Sync handler {handler.__name__}#{handler_id[-4:]} returned: {result}')
			# Cancel the monitor task since handler completed successfully
			monitor_task.cancel()

			# Record successful result
			event.event_result_update(handler=handler, eventbus=self, result=result)
			if handler_id in event.event_results:
				logger.debug(
					f'    ↳ Updated result for {handler.__name__}#{handler_id[-4:]}: {event.event_results[handler_id].status}'
				)
			else:
				logger.error(f'    ↳ ERROR: Result not found for {handler.__name__}#{handler_id[-4:]} after update!')
			return result

		except Exception as e:
			# Cancel the monitor task on error too
			monitor_task.cancel()

			# Record error
			event.event_result_update(handler=handler, eventbus=self, error=str(e))

			logger.exception(
				f'❌ {self} Error in handler {handler.__name__}#{str(id(handler))[-4:]}({event}): {type(e).__name__} {e}'
			)
			raise
		finally:
			# Reset context
			_current_event_context.reset(token)
			# Ensure monitor task is cancelled
			try:
				if not monitor_task.done():
					monitor_task.cancel()
				await monitor_task
			except asyncio.CancelledError:
				pass  # Expected when we cancel the monitor
			except Exception as e:
				# logger.debug(f"❌ {self} Handler monitor task cleanup error for {handler.__name__}#{str(id(handler))[-4:]}({event}): {type(e).__name__}: {e}")
				pass

	def _would_create_loop(self, event: BaseEvent, handler: EventHandler) -> bool:
		"""Check if calling this handler would create a loop (i.e. re-process an event that has already been processed by this EventBus)"""

		# First check: If handler is another EventBus.dispatch method, check if we're forwarding to another bus that it's already been processed by
		if hasattr(handler, '__self__') and isinstance(handler.__self__, EventBus) and handler.__name__ == 'dispatch':
			target_bus = handler.__self__
			if target_bus.name in event.event_path:
				return True

		# Second check: Check if there's already a completed result for this handler ID
		# This prevents the same handler from being called multiple times on the same event
		handler_id = str(id(handler))
		if handler_id in event.event_results:
			existing_result = event.event_results[handler_id]
			if existing_result.completed_at is not None:
				logger.debug(
					f'⚠️ Preventing loop: handler {handler.__name__} (id={handler_id}) already completed at {existing_result.completed_at} for event {event.event_id}'
				)
				return True

		return False

	async def _default_log_handler(self, event: BaseEvent) -> None:
		"""Default handler that logs all events"""
		# logger.debug(
		# 	f'✅ {self} completed: {event} -> {list(event.event_results.values()) or '<no handlers matched>'}'
		# )
		pass

	async def _default_wal_handler(self, event: BaseEvent) -> None:
		"""Persist completed event to WAL file as JSONL"""

		if not self.wal_path:
			return None

		try:
			event_json = event.model_dump_json()
			self.wal_path.parent.mkdir(parents=True, exist_ok=True)
			async with await anyio.open_file(self.wal_path, 'a') as f:
				await f.write(event_json + '\n')
		except Exception as e:
			logger.error(f'❌ {self} Failed to save event {event.event_id} to WAL file: {type(e).__name__} {e}\n{event}')
