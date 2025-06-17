import asyncio
import logging
import warnings
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, PrivateAttr
from uuid_extensions import uuid7str

from browser_use.utils import get_browser_use_version

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
	from browser_use.eventbus.service import EventBus

# Constants for validation
MAX_STRING_LENGTH = 10000  # 10K chars for most strings
MAX_URL_LENGTH = 2000
MAX_TASK_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000
MAX_FILE_CONTENT_SIZE = 50 * 1024 * 1024  # 50MB

UUIDStr = Annotated[str, AfterValidator(lambda s: str(UUID(s)))]


class BaseEvent(BaseModel):
	"""
	The base model used for all Events that flow through the EventBus system.
	"""
	
	model_config = {'arbitrary_types_allowed': True}

	event_schema: str | None = Field(default=None, description='Event schema version in format ClassName@version', max_length=100)
	event_type: str
	event_id: str = Field(default_factory=uuid7str)
	event_path: list[str] = Field(default_factory=list, description='Path tracking for event routing')
	parent_event_id: str | None = Field(default=None, description='ID of the parent event that triggered this event')

	# Completion tracking fields
	event_created_at: datetime = Field(
		default_factory=lambda: datetime.now(UTC), description='Timestamp when event was queued/created'
	)
	event_started_at: datetime | None = Field(default=None, description='Timestamp when event was started')
	event_completed_at: datetime | None = Field(default=None, description='Timestamp when event was completed')
	# Store results by handler id to avoid name clashes
	event_results: dict[str, Any] = Field(
		default_factory=dict, exclude=True, description='Handler results {str(id(handler)): result}'
	)
	event_errors: dict[str, str] = Field(
		default_factory=dict, exclude=True, description='Handler errors {str(id(handler)): error_str}'
	)
	# Map handler ids to metadata for result grouping
	_handler_metadata: dict[str, dict[str, Any]] = PrivateAttr(default_factory=dict)
	_event_completed_signal: asyncio.Event | None = PrivateAttr(default=None)
	_eventbus: 'EventBus | None' = PrivateAttr(default=None)
	results: 'EventResults | None' = Field(default=None, exclude=True)

	@property
	def state(self) -> str:
		return 'completed' if self.event_completed_at else 'started' if self.event_started_at else 'queued'

	def result(self, timeout: float = 30.0) -> 'EventResults':
		"""Return the EventResults object for accessing handler results"""
		if not self.results:
			raise RuntimeError("Event must be dispatched through an EventBus to use .result()")
		return self.results

	def record_result(self, handler: Any, result: Any, eventbus: 'EventBus | None' = None) -> None:
		"""Record a handler result with metadata"""
		# Special handling for EventResults from forwarded events
		if isinstance(result, EventResults):
			# Don't record the EventResults object itself
			# The forwarded event's results are already recorded in the same event
			return
		
		handler_id = str(id(handler))
		self.event_results[handler_id] = result
		self._handler_metadata[handler_id] = {
			'handler': handler,
			'name': handler.__name__,
			'eventbus': eventbus,
			'eventbus_name': eventbus.name if eventbus else None,
			'eventbus_id': str(id(eventbus)) if eventbus else None
		}
	
	def record_error(self, handler: Any, error: str) -> None:
		"""Record a handler error"""
		self.event_errors[str(id(handler))] = error
	
	def mark_complete(self) -> None:
		"""Mark event as completed"""
		self.event_completed_at = datetime.now(UTC)
		if self._event_completed_signal:
			self._event_completed_signal.set()

	def model_post_init(self, __context: Any) -> None:
		"""Initialize completion event and set event schema after model creation"""

		if self.event_schema is None:
			version = get_browser_use_version()
			self.event_schema = f'{self.__class__.__module__}.{self.__class__.__qualname__}@{version}'

		try:
			asyncio.get_running_loop()  # Only create event if we're in an async context
			self._event_completed_signal = asyncio.Event()
		except RuntimeError:
			self._event_completed_signal = None  # Not in async context, skip


class EventResults:
	"""Lazy mapping of handler results that supports efficient slicing and aggregation"""
	
	def __init__(self, event: BaseEvent, eventbus: 'EventBus', timeout: float = 30.0, include_wildcards: bool = False):
		self.event = event
		self.eventbus = eventbus
		self.timeout = timeout
		try:
			self._start_time = asyncio.get_event_loop().time()
		except RuntimeError:
			# No event loop in sync context, use time.time()
			import time
			self._start_time = time.time()
		self.include_wildcards = include_wildcards
		
		# Only precompute the first handler for efficient .first() access
		event_key = event.event_type
		specific_handlers = self.eventbus.handlers.get(event_key, [])
		wildcard_handlers = self.eventbus.handlers.get('*', []) if include_wildcards else []
		
		local_handlers = specific_handlers + wildcard_handlers
		self._first_handler = local_handlers[0] if local_handlers else None
		
		# Track EventBuses that have dispatched this event
		self._seen_eventbus_ids: set[str] = {str(id(eventbus))}
	
	def __getitem__(self, key: str | Any):
		"""Get by handler ID, handler name, or handler function"""
		if callable(key):
			# Convert handler function to its ID
			key = str(id(key))
		if not isinstance(key, str):
			raise TypeError("EventResults accepts handler IDs (str), handler names (str), or handler functions")
		
		# First try as handler ID
		if key in self.event.event_results:
			return self.event.event_results[key]
		
		# Then try as handler name - find first matching handler
		for handler_id, metadata in self.event._handler_metadata.items():
			if metadata['name'] == key and handler_id in self.event.event_results:
				return self.event.event_results[handler_id]
		
		raise KeyError(f"No result found for key: {key}")
	
	def __await__(self):
		"""Default to by_handler_id() when awaited directly"""
		return self.by_handler_id().__await__()
	
	async def _wait_for_all_handlers(self) -> None:
		"""Wait for all handlers across all seen EventBuses to complete"""
		start_time = asyncio.get_event_loop().time()
		
		while True:
			# Check if we've timed out
			if asyncio.get_event_loop().time() - start_time > self.timeout:
				logger.warning(f"Timeout waiting for all handlers after {self.timeout}s")
				break
			
			# Get all eventbus IDs that have registered handlers
			seen_handler_buses = set()
			for handler_id, metadata in self.event._handler_metadata.items():
				if metadata.get('eventbus_id'):
					seen_handler_buses.add(metadata['eventbus_id'])
			
			# Check if all seen buses have registered their handlers
			all_buses_handled = self._seen_eventbus_ids.issubset(seen_handler_buses)
			
			# Check if all registered handlers have results or errors
			all_handlers_done = all(
				handler_id in self.event.event_results or handler_id in self.event.event_errors
				for handler_id in self.event._handler_metadata
			)
			
			if all_buses_handled and all_handlers_done:
				break
				
			# Small delay before checking again
			await asyncio.sleep(0.01)
	
	async def _wait_for_handler(self, handler: Any) -> Any:
		"""Wait for a specific handler by id"""
		handler_id = str(id(handler))
		while handler_id not in self.event.event_results and not self._is_timeout():
			await asyncio.sleep(0.001)
		return self.event.event_results.get(handler_id)
	
	async def _get_all_results(self) -> dict[str, Any]:
		"""Wait for all handlers to complete and return all results"""
		await self._wait_for_all_handlers()
		return self.event.event_results
	
	async def first(self, default=None) -> Any:
		"""Get first handler result - only waits for the first handler"""
		if not self._first_handler:
			return default
		return await self._wait_for_handler(self._first_handler) or default
	
	async def last(self) -> Any:
		"""Get last handler result - waits for all handlers to complete"""
		await self._wait_for_all_handlers()
		
		# Get all handlers from metadata in order
		handler_items = list(self.event._handler_metadata.items())
		
		if handler_items:
			# Get the last handler
			handler_id = handler_items[-1][0]
			return self.event.event_results.get(handler_id)
		
		return None
	
	
	async def by_handler_name(self) -> dict[str, Any]:
		"""Get results keyed by handler name (last handler with each name wins)"""
		await self._wait_for_all_handlers()
		
		results = {}
		for handler_id, metadata in self.event._handler_metadata.items():
			if handler_id in self.event.event_results:
				value = self.event.event_results[handler_id]
				if value is not None:
					results[metadata['name']] = value
		return results
	
	async def by_handler_id(self) -> dict[str, Any]:
		"""Get results keyed by handler id string"""
		await self._wait_for_all_handlers()
		
		results = {}
		for handler_id in self.event.event_results:
			value = self.event.event_results[handler_id]
			if value is not None:
				results[handler_id] = value
		return results
	
	async def by_eventbus_id(self) -> dict[str, Any]:
		"""Get results keyed by eventbus id (last handler per eventbus wins)"""
		await self._wait_for_all_handlers()
		
		results = {}
		for handler_id, metadata in self.event._handler_metadata.items():
			if handler_id in self.event.event_results:
				eventbus_id = metadata.get('eventbus_id')
				value = self.event.event_results[handler_id]
				if value is not None and eventbus_id:
					results[eventbus_id] = value
		return results
	
	async def by_path(self) -> dict[str, Any]:
		"""Get results keyed by path: eventbus_name#eventbus_id.handler_name"""
		await self._wait_for_all_handlers()
		
		results = {}
		for handler_id, metadata in self.event._handler_metadata.items():
			if handler_id in self.event.event_results:
				if metadata.get('eventbus_name') and metadata.get('eventbus_id'):
					path = f"{metadata['eventbus_name']}#{metadata['eventbus_id']}.{metadata['name']}"
					value = self.event.event_results[handler_id]
					if value is not None:
						results[path] = value
		return results
	
	async def values(self) -> list[Any]:
		"""Get all results as list"""
		await self._wait_for_all_handlers()
		
		# Return results in handler registration order
		results = []
		for handler_id in self.event._handler_metadata:
			if handler_id in self.event.event_results:
				value = self.event.event_results[handler_id]
				if value is not None:
					results.append(value)
		return results
	
	async def flat_dict(self) -> dict[str, Any]:
		"""Merge results into single dict"""
		await self._wait_for_all_handlers()
		
		merged = {}
		for handler_id, metadata in self.event._handler_metadata.items():
			if handler_id in self.event.event_results:
				result = self.event.event_results[handler_id]
				if result is not None:
					if not isinstance(result, dict):
						handler_name = metadata.get('name', 'unknown')
						raise TypeError(f"Handler '{handler_name}' returned {type(result).__name__} instead of dict")
					merged.update(result)
		return merged
	
	async def flat_list(self) -> list[Any]:
		"""Merge results into single list"""
		await self._wait_for_all_handlers()
		
		merged = []
		for handler_id, metadata in self.event._handler_metadata.items():
			if handler_id in self.event.event_results:
				result = self.event.event_results[handler_id]
				if result is not None:
					if not isinstance(result, list):
						handler_name = metadata.get('name', 'unknown')
						raise TypeError(f"Handler '{handler_name}' returned {type(result).__name__} instead of list")
					merged.extend(result)
		return merged
	
	def _is_timeout(self) -> bool:
		"""Check timeout"""
		try:
			current_time = asyncio.get_event_loop().time()
		except RuntimeError:
			# No event loop in sync context, use time.time()
			import time
			current_time = time.time()
		return current_time - self._start_time > self.timeout
