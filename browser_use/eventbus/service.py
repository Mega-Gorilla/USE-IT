import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Union

import anyio
from pydantic import BaseModel

from browser_use.eventbus.models import BaseEvent

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Union[Callable[[BaseEvent], Any], Callable[[BaseEvent], Awaitable[Any]]]


class EventBus:
	"""
	Async event bus with write-ahead logging and guaranteed FIFO processing.

	Features:
	- Sync and async event enqueueing
	- Blocking and non-blocking dispatch
	- Write-ahead logging with UUIDs and timestamps
	- FIFO event processing
	- Parallel handler execution per event
	- Serial event processing
	"""

	def __init__(self, name: str | None = None, wal_path: Path | str | None = None):
		self.name = name or f'EventBus_{hex(id(self))[-6:]}'
		self.event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
		self.event_history: list[BaseEvent] = []
		self.handlers: dict[str, list[EventHandler]] = defaultdict(list)
		self._is_running = False
		self._runloop_task: asyncio.Task | None = None
		self._runloop_lock = asyncio.Lock()

		# Set up WAL path and create parent directory if needed
		self.wal_path = None
		if wal_path:
			self.wal_path = Path(wal_path)
			self.wal_path.parent.mkdir(parents=True, exist_ok=True)

		# Signal that fires when the event bus is idle (no events being processed)
		self._on_idle = asyncio.Event()
		self._on_idle.set()  # Start in idle state

		# Register default logger handler
		self.on('*', self._default_log_handler)

	def __del__(self):
		"""Auto-cleanup on garbage collection"""
		if self._is_running:
			try:
				loop = asyncio.get_running_loop()
				loop.create_task(self.stop())
			except RuntimeError:
				# No event loop, nothing to clean
				pass

	@property
	def events_queued(self) -> list[BaseEvent]:
		"""Get events that haven't started processing yet"""
		return [event for event in self.event_history if event.started_at is None]

	@property
	def events_started(self) -> list[BaseEvent]:
		"""Get events currently being processed"""
		return [event for event in self.event_history if event.started_at and not event.completed_at]

	@property
	def events_completed(self) -> list[BaseEvent]:
		"""Get events that have completed processing"""
		return [event for event in self.event_history if event.completed_at is not None]

	def on(self, event_pattern: str | type[BaseModel], handler: EventHandler) -> None:
		"""Subscribe to events matching a pattern, event type name, or event model class.
		Use '*' for all events.

		Examples:
			eventbus.on('*', handler)  # All events
			eventbus.on('TaskStartedEvent', handler)  # Specific event type
			eventbus.on(TaskStartedEvent, handler)  # Event model class
		"""
		# Allow both sync and async handlers
		if event_pattern == '*':
			# Subscribe to all events using '*' as the key
			self.handlers['*'].append(handler)
		elif isinstance(event_pattern, type) and issubclass(event_pattern, BaseModel):
			# Subscribe by model class
			self.handlers[event_pattern.__name__].append(handler)
		else:
			# Subscribe by string event type
			self.handlers[str(event_pattern)].append(handler)

	def emit(self, event: BaseEvent) -> BaseEvent:
		"""
		Enqueue an event for processing. Returns awaitable event object.
		Auto-starts the EventBus if not already running.
		"""
		# Auto-start if not running
		if not self._is_running:
			try:
				loop = asyncio.get_running_loop()
				self._is_running = True
				self._runloop_task = loop.create_task(self._run_loop())
			except RuntimeError:
				# No event loop - will start when one becomes available
				pass

		# All events must inherit from BaseEvent
		actual_event = event

		# Add this EventBus to the event_path if not already there
		if self.name not in actual_event.event_path:
			actual_event = actual_event.model_copy(update={'event_path': actual_event.event_path + [self.name]})

		self.event_history.append(actual_event)

		# Put event in queue synchronously using put_nowait
		try:
			self.event_queue.put_nowait(actual_event)
		except asyncio.QueueFull:
			logger.error(f'Event queue is full, dropping event {actual_event.event_type}')

		logger.debug(f'Emitting event {actual_event.event_type} to queue')
		return actual_event

	async def stop(self, timeout: float | None = None) -> None:
		"""Stop the event bus, optionally waiting for events to complete"""
		if not self._is_running:
			return

		# Log current state
		logger.debug(f'EventBus stopping - Queue size: {self.event_queue.qsize()}, Processing events: {len(self.events_started)}')

		# Wait for completion if timeout specified
		if timeout is not None:
			await self.wait_until_idle(timeout=timeout)

		# Force shutdown
		self._is_running = False

		if self._runloop_task:
			self._runloop_task.cancel()
			try:
				await self._runloop_task
			except asyncio.CancelledError:
				pass

		if timeout is not None:
			logger.debug(f'{self.name} stopped gracefully')
		else:
			logger.debug(f'{self.name} stopped immediately')

	async def wait_until_idle(self, timeout: float | None = None) -> None:
		"""Wait until the event bus is idle (no events being processed)"""
		# First wait for the queue to be empty
		await self.event_queue.join()
		logger.debug(f'Queue joined, processing events: {len(self.events_started)}')

		# Then wait for idle state with timeout
		try:
			await asyncio.wait_for(self._on_idle.wait(), timeout=timeout)
		except TimeoutError:
			logger.warning(f'Timeout waiting for event bus to be idle after {timeout}s (processing: {len(self.events_started)})')

	def _get_applicable_handlers(self, event: BaseEvent) -> dict[str, EventHandler]:
		"""Get all handlers that should process the given event, filtering out those that would create loops"""
		applicable_handlers = []

		# Add type-specific handlers
		applicable_handlers.extend(self.handlers.get(event.event_type, []))

		# Add wildcard handlers (handlers registered for '*')
		applicable_handlers.extend(self.handlers.get('*', []))

		# Filter out handlers that would create loops and build name->handler mapping
		filtered_handlers = {}
		for handler in applicable_handlers:
			if self._would_create_loop(handler, event):
				logger.debug(f'Skipping {handler.__name__} to prevent loop for {event.event_type}')
			else:
				filtered_handlers[handler.__name__] = handler

		return filtered_handlers

	async def _execute_handlers(self, event: BaseEvent) -> None:
		"""Execute all handlers for an event in parallel"""
		applicable_handlers = self._get_applicable_handlers(event)

		if not applicable_handlers:
			return

		# Execute all handlers in parallel
		handler_tasks = {}
		for handler_name, handler in applicable_handlers.items():
			task = asyncio.create_task(self._safe_execute_handler(handler, event))
			handler_tasks[handler_name] = task

		# Wait for all handlers to complete and build results
		for handler_name, task in handler_tasks.items():
			try:
				result = await task
				event.results[handler_name] = result
			except Exception as e:
				event.errors[handler_name] = str(e)
				logger.error(f'Handler {handler_name} failed for event {event.event_id}: {e}')

	async def _safe_execute_handler(self, handler: EventHandler, event: BaseEvent) -> Any:
		"""Safely execute a single handler"""
		try:
			if inspect.iscoroutinefunction(handler):
				return await handler(event)
			else:
				# Run sync handler in thread pool to avoid blocking
				loop = asyncio.get_event_loop()
				return await loop.run_in_executor(None, handler, event)
		except Exception as e:
			logger.exception(f'Error in handler {handler.__name__} for event {event.event_id}')
			raise

	async def _run_loop(self) -> None:
		"""Main event processing loop"""
		while self._is_running:
			try:
				# Check if we're idle
				if self.event_queue.empty() and len(self.events_started) == 0:
					self._on_idle.set()
				else:
					self._on_idle.clear()

				# Wait for next event with timeout to periodically check idle state
				try:
					event = await asyncio.wait_for(self.event_queue.get(), timeout=0.1)

					# Clear idle state when we get an event
					self._on_idle.clear()

					# Process the event
					async with self._runloop_lock:
						# Record start time
						event.started_at = datetime.now(UTC)

						# Execute all handlers for this event
						await self._execute_handlers(event)

						# Mark event as completed
						event.mark_completed()

						# Persist to WAL if configured
						if self.wal_path:
							wal_result = await self._default_wal_handler(event)
							# logger.debug(f'WAL persistence result: {wal_result}')

						# Mark task as done
						self.event_queue.task_done()

				except TimeoutError:
					# No events in queue, continue to check idle state
					continue
			except asyncio.CancelledError:
				break
			except Exception as e:
				logger.exception(f'{self.name} Error in event loop: {e}')
				# Continue running even if there's an error

	@staticmethod
	def _would_create_loop(handler: EventHandler, event: BaseEvent) -> bool:
		"""Check if calling this handler would create a loop"""
		# If handler is another EventBus.emit method
		if hasattr(handler, '__self__') and isinstance(handler.__self__, EventBus):
			target_bus = handler.__self__
			return target_bus.name in event.event_path
		return False

	@staticmethod
	async def _default_log_handler(event: BaseEvent) -> str:
		"""Default handler that logs all events"""
		logger.debug(f'Event processed: {event.event_type} [{event.event_id}] - {event.model_dump_json()}')
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
			logger.error(f'Failed to persist event {event.event_id} to WAL: {e}')
			return 'failed'
