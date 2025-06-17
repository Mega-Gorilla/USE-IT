import asyncio
import inspect
import logging
import warnings
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, copy_context
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Union

import anyio
from pydantic import BaseModel
from uuid_extensions import uuid7str

from browser_use.eventbus.models import BaseEvent, UUIDStr

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Union[Callable[[BaseEvent], Any], Callable[[BaseEvent], Awaitable[Any]]]

# Context variable to track the current event being processed
_current_event_context: ContextVar[BaseEvent | None] = ContextVar('current_event', default=None)


class EventBus:
	"""
	Async event bus with write-ahead logging and guaranteed FIFO processing.

	Features:
	- Enqueue events synchronously, await their results using await event.result()
	- FIFP Write-ahead logging with UUIDs and timestamps,
	- Serial event processing, parallel handler execution per event
	"""

	name: str
	event_queue: asyncio.Queue[BaseEvent] | None
	event_history: dict[UUIDStr, BaseEvent]
	handlers: dict[str, list[EventHandler]]
	wal_path: Path | None = None
	parallel_handlers: bool = False

	id: UUIDStr
	_is_running: bool = False
	_runloop_task: asyncio.Task | None = None
	_runloop_lock: asyncio.Lock | None = None
	_on_idle: asyncio.Event | None = None

	def __init__(self, name: str | None = None, wal_path: Path | str | None = None, parallel_handlers: bool = False):
		self.id = uuid7str()
		self.name = name or f'EventBus_{self.id[-8:]}'
		assert self.name.isidentifier(), f'EventBus name must be a unique identifier string, got: {self.name}'
		self.event_queue = None
		self.event_history = {}
		self.handlers = defaultdict(list)
		self.parallel_handlers = parallel_handlers
		self._runloop_lock = None
		self._on_idle = None

		# Set up WAL path and create parent directory if needed
		if wal_path:
			self.wal_path = Path(wal_path)
			self.wal_path.parent.mkdir(parents=True, exist_ok=True)

		# Register default logger handler
		self.on('*', self._default_log_handler)

	def __del__(self):
		"""Auto-cleanup on garbage collection"""
		if self._is_running:
			self._is_running = False
			if self._runloop_task and not self._runloop_task.done():
				try:
					self._runloop_task.cancel()
				except RuntimeError:
					pass

	def __str__(self) -> str:
		return self.name

	def __repr__(self) -> str:
		return self.name

	@property
	def events_queued(self) -> list[BaseEvent]:
		"""Get events that haven't started processing yet"""
		return [event for event in self.event_history.values() if event.event_started_at is None]

	@property
	def events_started(self) -> list[BaseEvent]:
		"""Get events currently being processed"""
		return [event for event in self.event_history.values() if event.event_started_at and not event.event_completed_at]

	@property
	def events_completed(self) -> list[BaseEvent]:
		"""Get events that have completed processing"""
		return [event for event in self.event_history.values() if event.event_completed_at is not None]

	def on(self, event_pattern: str | type[BaseModel], handler: EventHandler) -> None:
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
		handler_name = handler.__name__
		existing_names = [h.__name__ for h in self.handlers.get(event_key, [])]
		
		if handler_name in existing_names:
			warnings.warn(
				f"⚠️  Handler '{handler_name}' already registered for event '{event_key}'. "
				f"This may cause ambiguous results when using name-based access. "
				f"Consider using unique function names.",
				UserWarning,
				stacklevel=2
			)
		
		# Register handler
		self.handlers[event_key].append(handler)
		logger.debug(f"✅ {self} Registered handler {handler_name} for event {event_key}")
		
		# Auto-start if needed (but not for the default logger)
		if not self._is_running and handler_name != '_default_log_handler':
			self._start()

	def dispatch(self, event: BaseEvent, timeout: float = 30.0) -> BaseEvent:
		"""
		Enqueue an event for processing and return the event.
		(Auto-starts the EventBus's async _run_loop() if not already running)

		Returns the event which can be awaited using event.result() or other query methods.
		"""
		assert event.event_id, 'Missing event.event_id: UUIDStr = uuid7str()'
		assert event.event_created_at, 'Missing event.queued_at: datetime = datetime.now(UTC)'
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

		assert event.event_path, 'Missing event.event_path: list[str] (with at least the origin function name recorded in it)'
		assert all(entry.isidentifier() for entry in event.event_path), (
			f'Event.event_path must be a list of valid EventBus names, got: {event.event_path}'
		)

		# Add event to history
		self.event_history[event.event_id] = event

		self._start()

		# Put event in queue synchronously using put_nowait
		if self.event_queue:
			try:
				logger.debug(f'📋 {self} Dispatching event {event.event_type} to queue')
				self.event_queue.put_nowait(event)
			except asyncio.QueueFull:
				logger.error(f'⚠️ {self} Event queue is full! Dropping event {event.event_type}:\n{event.model_dump_json()}')

		# Record pending results for all applicable handlers
		applicable_handlers = self._get_applicable_handlers(event)
		for handler in applicable_handlers.values():
			event.event_result_update(handler=handler, eventbus=self, status='pending')
		
		return event

	async def expect(
		self,
		event_type: str | type[BaseModel],
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
		
		def temporary_handler(event: BaseEvent) -> None:
			"""Handler that resolves the future when a matching event is found"""
			if not future.done() and (predicate is None or predicate(event)):
				future.set_result(event)
		
		# Register temporary handler
		self.on(event_type, temporary_handler)
		
		try:
			# Wait for the future with optional timeout
			return await asyncio.wait_for(future, timeout=timeout)
		finally:
			# Clean up handler
			event_key = event_type.__name__ if isinstance(event_type, type) else str(event_type)
			if event_key in self.handlers and temporary_handler in self.handlers[event_key]:
				self.handlers[event_key].remove(temporary_handler)

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
					self._on_idle.set()  # Start in idle state
				# Create and start the run loop task
				self._runloop_task = loop.create_task(self._run_loop())
				self._is_running = True
			except RuntimeError:
				pass  # No event loop - will start when one becomes available

	async def stop(self, timeout: float | None = None) -> None:
		"""Stop the event bus, optionally waiting for events to complete"""
		if not self._is_running:
			return

		# Log current state

		# Wait for completion if timeout specified
		if timeout is not None:
			await self.wait_until_idle(timeout=timeout)

		queue_size = self.event_queue.qsize() if self.event_queue else 0
		if queue_size or self.events_queued or self.events_started:
			logger.warning(
				f'⚠️ {self} stopping with pending events: Queued {queue_size} | Pending {len(self.events_queued)} | Processing {len(self.events_started)} | Completed {len(self.events_completed)}'
			)

		# Force shutdown
		self._is_running = False
		if self._runloop_task:
			self._runloop_task.cancel()
			try:
				await self._runloop_task
			except asyncio.CancelledError:
				pass

		logger.debug(f'⏹ {self} stopped gracefully' if timeout is not None else f'🛑 {self} stopped immediately')

	async def wait_until_idle(self, timeout: float | None = None) -> None:
		"""Wait until the event bus is idle (no events being processed)"""

		self._start()

		# First wait for the queue to be empty
		await self.event_queue.join()
		# logger.debug(f'Queue joined, processing events: {len(self.events_started)}')

		# Then wait for idle state with timeout
		if self._on_idle:
			try:
				await asyncio.wait_for(self._on_idle.wait(), timeout=timeout)
			except TimeoutError:
				logger.warning(
					f'⌛️ {self} Timeout waiting for event bus to be idle after {timeout}s (processing: {len(self.events_started)})'
				)

	async def _run_loop(self) -> None:
		"""Main event processing loop"""
		while self._is_running:
			try:
				# Ensure we have async objects
				if not self.event_queue:
					logger.error(f'❌ {self} Event queue not initialized in _run_loop')
					break
					
				# Check if we're idle
				if self._on_idle:
					if self.event_queue.empty() and len(self.events_started) == 0:
						self._on_idle.set()
					else:
						self._on_idle.clear()

				try:
					await self._run_loop_step()
				except TimeoutError:
					continue  # No events in queue, continue to idle
			except asyncio.CancelledError:
				break
			except Exception as e:
				logger.exception(f'❌ {self} Error in event loop: {type(e).__name__} {e}')
				# Continue running even if there's an error

	async def _run_loop_step(self, event: BaseEvent | None = None) -> BaseEvent:
		"""Process a single event from the queue"""

		# Wait for next event with timeout to periodically check idle state
		event = event or await asyncio.wait_for(self.event_queue.get(), timeout=0.1)

		# Clear idle state when we get an event
		if self._on_idle:
			self._on_idle.clear()

		# Process the event
		if self._runloop_lock:
			async with self._runloop_lock:
				event.event_started_at = datetime.now(UTC)

				# Execute all handlers for this event
				applicable_handlers = self._get_applicable_handlers(event)
				await self._execute_handlers(event, handlers=applicable_handlers)

				# Mark event as completed
				event.event_completed_at = datetime.now(UTC)

				# Persist to WAL if configured
				if self.wal_path:
					wal_result = await self._default_wal_handler(event)
					# logger.debug(f'WAL persistence result: {wal_result}')

				# Mark task as done
				self.event_queue.task_done()
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
				logger.debug(f'Skipping {handler.__name__} to prevent loop for {event.event_type}')
				continue
			else:
				handler_id = str(id(handler))
				filtered_handlers[handler_id] = handler

		return filtered_handlers

	async def _execute_handlers(self, event: BaseEvent, handlers: dict[str, EventHandler] | None = None) -> None:
		"""Execute all handlers for an event in parallel"""
		applicable_handlers = handlers if (handlers is not None) else self._get_applicable_handlers(event)
		if not applicable_handlers:
			return

		# Execute all handlers in parallel
		if self.parallel_handlers:
			handler_tasks = {}
			for handler_id, handler in applicable_handlers.items():
				task = asyncio.create_task(self._execute_sync_or_async_handler(event, handler))
				handler_tasks[handler_id] = (task, handler)

			# Wait for all handlers to complete and record results incrementally
			for handler_id, (task, handler) in handler_tasks.items():
				try:
					result = await task
					event.event_result_update(handler=handler, eventbus=self, result=result)
				except Exception as e:
					event.event_result_update(handler=handler, eventbus=self, error=str(e))
					logger.error(
						f'❌ {self} Handler {handler.__name__} failed for event {event.event_id}: {type(e).__name__} {e}\n{event.model_dump()}'
					)
		else:
			# otherwise, execute handlers serially, wait until each one completes before moving on to the next
			for handler_id, handler in applicable_handlers.items():
				try:
					# Mark handler as started
					event.event_result_update(handler=handler, eventbus=self, status='started')
					
					# Execute the handler
					result = await self._execute_sync_or_async_handler(event, handler)
					
					# Record successful result
					event.event_result_update(handler=handler, eventbus=self, result=result)
				except Exception as e:
					# Record error
					event.event_result_update(handler=handler, eventbus=self, error=str(e))
					logger.error(
						f'❌ {self} Handler {handler.__name__} failed for event {event.event_id}: {type(e).__name__} {e}\n{event.model_dump()}'
					)

	async def _execute_sync_or_async_handler(self, event: BaseEvent, handler: EventHandler) -> Any:
		"""Safely execute a single handler"""
		# Set the current event in context so child events can reference it
		token = _current_event_context.set(event)
		try:
			if inspect.iscoroutinefunction(handler):
				return await handler(event)
			else:
				# Run sync handler in thread pool with context preserved
				loop = asyncio.get_event_loop()
				ctx = copy_context()
				# Create a wrapper that preserves the context
				def context_preserving_wrapper():
					# The context is already copied, just run the handler
					return handler(event)
				return await loop.run_in_executor(None, ctx.run, context_preserving_wrapper)
		except Exception as e:
			logger.exception(
				f'❌ {self} Error in handler {handler.__name__} for event {event.event_id}: {type(e).__name__} {e}\n{event.model_dump()}'
			)
			raise
		finally:
			# Reset context
			_current_event_context.reset(token)

	@staticmethod
	def _would_create_loop(event: BaseEvent, handler: EventHandler) -> bool:
		"""Check if calling this handler would create a loop (i.e. re-process an event that has already been processed by this EventBus)"""
		# If handler is another EventBus.dispatch method
		if hasattr(handler, '__self__') and isinstance(handler.__self__, EventBus) and handler.__name__ == 'dispatch':
			target_bus = handler.__self__
			return target_bus.name in event.event_path
		return False

	async def _default_log_handler(self, event: BaseEvent) -> str:
		"""Default handler that logs all events"""
		logger.debug(
			f'✅ {self} Event processed: {event.event_type} [{event.event_id}] -> {len(event.event_results)} results @ {event.event_completed_at}'
		)
		return 'logged'

	async def _default_wal_handler(self, event: BaseEvent) -> str:
		"""Persist completed event to WAL file as JSONL"""
		if not self.wal_path:
			return 'skipped'

		try:
			# Use model_dump_json which already handles datetime serialization
			event_json = event.model_dump_json()

			# Append as JSONL (one JSON object per line)
			async with await anyio.open_file(self.wal_path, 'a') as f:
				await f.write(event_json + '\n')

			return 'appended'

		except Exception as e:
			logger.error(
				f'❌ {self} Failed to save event {event.event_id} to WAL file: {type(e).__name__} {e}\n{event.model_dump()}'
			)
			return 'failed'
