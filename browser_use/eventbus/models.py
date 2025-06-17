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
	event_parent_id: str | None = Field(default=None, description='ID of the parent event that triggered this event')

	# Completion tracking fields
	event_created_at: datetime = Field(
		default_factory=lambda: datetime.now(UTC), description='Timestamp when event was queued/created'
	)
	event_started_at: datetime | None = Field(default=None, description='Timestamp when event was started')
	event_completed_at: datetime | None = Field(default=None, description='Timestamp when event was completed')
	
	# Results indexed by handler ID
	event_results: dict[str, 'EventResult'] = Field(default_factory=dict, exclude=True)
	
	# Completion signal
	_event_completed: asyncio.Event | None = PrivateAttr(default=None)

	@property
	def event_completed(self) -> asyncio.Event | None:
		"""Lazily create asyncio.Event when accessed"""
		if self._event_completed is None:
			try:
				asyncio.get_running_loop()
				self._event_completed = asyncio.Event()
			except RuntimeError:
				pass  # Keep it None if no event loop
		return self._event_completed

	@property
	def state(self) -> str:
		return 'completed' if self.event_completed_at else 'started' if self.event_started_at else 'queued'

	def model_post_init(self, __context: Any) -> None:
		"""Initialize completion event and set event schema after model creation"""
		if self.event_schema is None:
			version = get_browser_use_version()
			self.event_schema = f'{self.__class__.__module__}.{self.__class__.__qualname__}@{version}'

	def event_result_update(self, handler: Any=None, eventbus: 'EventBus'=None, **kwargs) -> None:
		"""Create or update an EventResult for a handler"""
		handler_id = str(id(handler))
		eventbus = eventbus or (handler and hasattr(handler, '__self__') and handler.__self__) or None
		eventbus_id = str(id(eventbus))

		# Get or create EventResult
		if handler_id not in self.event_results:
			self.event_results[handler_id] = EventResult(
				handler_id=handler_id,
				handler_name=handler and handler.__name__ or '??unknown_handler??',
				eventbus_id=eventbus_id,
				eventbus_name=eventbus and eventbus.name or '??unknown_eventbus??',
				event_parent_id=self.event_id,
				status=kwargs.get('status', 'pending'),
			)
		
		# Update the EventResult with provided kwargs
		self.event_results[handler_id].update(**kwargs)
		self._check_completion()

	def _check_completion(self) -> None:
		"""Check if all handlers are done and signal completion"""
		if self.event_completed and not self.event_completed.is_set():
			all_done = all(
				result.status in ('completed', 'error')
				for result in self.event_results.values()
			)
			if all_done:
				self.event_completed_at = datetime.now(UTC)
				self.event_completed.set()
	
	async def event_results_by_handler_id(self) -> dict[str, Any]:
		"""Get results keyed by handler ID"""
		# Wait for completion
		if self.event_completed:
			await self.event_completed.wait()
		
		results = {}
		for handler_id, event_result in self.event_results.items():
			if event_result.status == 'completed' and event_result.result is not None:
				results[handler_id] = event_result.result
		return results
	
	async def event_results_flat_dict(self) -> dict[str, Any]:
		"""Merge all dict results into single dict"""

		await self.event_completed.wait()
		
		merged = {}
		for event_result in self.event_results.values():
			if event_result.status == 'completed' and event_result.result is not None:
				if not event_result.result:  # skip if result is {} or None
					continue
				if not isinstance(event_result.result, dict):
					raise TypeError(f"Handler '{event_result.handler_name}' returned {type(event_result.result).__name__} instead of dict")
				merged.update(event_result.result)  # update the merged dict with the contents of the result dict
		return merged
	
	async def event_results_flat_list(self) -> list[Any]:
		"""Merge all list results into single list"""

		await self.event_completed.wait()
		
		merged = []
		for event_result in self.event_results.values():
			if event_result.status == 'completed' and event_result.result is not None:
				if isinstance(event_result.result, list):
					merged.extend(event_result.result)  # append the contents of the list to the merged list
				else:
					merged.append(event_result.result)  # append individual item to the merged list
		return merged

	async def event_result(self, timeout: float = 30.0) -> Any:
		results = await self.event_results_flat_list()
		return results[0] if results else None
	
	async def result(self, timeout: float = 30.0) -> 'BaseEvent':
		"""Wait for event to complete and return self"""
		if self.event_completed:
			await self.event_completed.wait()
		return self


class EventResult(BaseModel):
	"""Individual result from a single handler"""
	
	id: str = Field(default_factory=uuid7str)
	handler_id: str
	handler_name: str
	eventbus_id: str
	eventbus_name: str
	status: str = 'pending'  # pending, started, completed, error
	result: Any = None
	error: str | None = None
	started_at: datetime | None = None
	completed_at: datetime | None = None
	event_parent_id: str | None = None
	
	# Completion signal  
	_completed: asyncio.Event | None = PrivateAttr(default=None)

	@property
	def completed(self) -> asyncio.Event | None:
		"""Lazily create asyncio.Event when accessed"""
		if self._completed is None:
			try:
				asyncio.get_running_loop()
				self._completed = asyncio.Event()
			except RuntimeError:
				pass  # Keep it None if no event loop
		return self._completed
	# _event: BaseEvent | None = PrivateAttr(default=None)       # in a db we'd store a foreign key to the event
	
	model_config = {'arbitrary_types_allowed': True}

	def __str__(self) -> str:
		handler_qualname = f'{self.eventbus_name}.{self.handler_name}'
		return f"{handler_qualname}() -> {self.result or self.error or '...'} ({self.status})"
	
	
	def __await__(self):
		"""Wait for this result to complete and return the result or raise error"""
		async def wait_and_return():
			await self.completed.wait()
			
			if self.status == 'error' and self.error:
				raise RuntimeError(f"Handler {self.handler_name} failed: {self.error}")
			
			return self.result
		
		return wait_and_return().__await__()
	
	def update(self, **kwargs) -> None:
		"""Update the EventResult with provided kwargs"""
		if 'result' in kwargs:
			self.result = kwargs['result']
			self.status = 'completed'
			self.completed_at = datetime.now(UTC)
			if self.completed:
				self.completed.set()
		elif 'error' in kwargs:
			self.error = kwargs['error']
			self.status = 'error'
			self.completed_at = datetime.now(UTC)
			if self.completed:
				self.completed.set()
		elif 'status' in kwargs:
			self.status = kwargs['status']
			if self.status == 'started' and not self.started_at:
				self.started_at = datetime.now(UTC)


# Resolve forward references
BaseEvent.model_rebuild()
