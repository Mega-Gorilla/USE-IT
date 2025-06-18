import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str

from browser_use.utils import get_browser_use_version

if TYPE_CHECKING:
	from browser_use.eventbus.service import EventBus


logger = logging.getLogger(__name__)


def is_valid_event_name(s: str) -> bool:
	assert str(s).isidentifier() and not str(s).startswith('_'), f'Invalid event name: {s}'
	return str(s)


def is_valid_python_id(s: str) -> bool:
	assert str(s).isdigit(), f'Invalid Python ID: {s}'
	return str(s)


UUIDStr = Annotated[str, AfterValidator(lambda s: str(UUID(s)))]
PythonIdStr = Annotated[str, AfterValidator(is_valid_python_id)]
PythonIdentifierStr = Annotated[str, AfterValidator(is_valid_event_name)]
EventHandler = Callable[['BaseEvent'], Any] | Callable[['BaseEvent'], Awaitable[Any]]


class BaseEvent(BaseModel):
	"""
	The base model used for all Events that flow through the EventBus system.
	"""

	model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, validate_default=True)

	event_type: PythonIdentifierStr
	event_schema: str | None = Field(default=None, description='Event schema version in format ClassName@version', max_length=100)
	event_timeout: float | None = Field(default=60.0, description='Timeout in seconds for event to complete')

	# Runtime metadata
	event_id: UUIDStr = Field(default_factory=uuid7str, max_length=36)
	event_path: list[PythonIdentifierStr] = Field(default_factory=list, description='Path tracking for event routing')
	event_parent_id: UUIDStr | None = Field(
		default=None, description='ID of the parent event that triggered this event', max_length=36
	)

	# Completion tracking fields
	event_created_at: datetime = Field(
		default_factory=lambda: datetime.now(UTC),
		description='Timestamp when event was first dispatched to an EventBus aka marked pending',
	)

	event_results: dict[PythonIdStr, 'EventResult'] = Field(
		default_factory=dict, exclude=True
	)  # Results indexed by str(id(handler_func))

	# Completion signal
	_event_completed: asyncio.Event | None = PrivateAttr(default=None)
	_event_processed_at: datetime | None = PrivateAttr(default=None)

	def __str__(self) -> str:
		icon = (
			'⏳'
			if self.event_status == 'pending'
			else '✅'
			if self.event_status == 'completed'
			else '❌'
			if self.event_status == 'error'
			else '🏃'
		)
		return f'{self.__class__.__name__}#{self.event_id[-4:]}{icon}{">".join(self.event_path[1:])})'

	def _log_safe_summary(self) -> str:
		"""only event metadata without contents, avoid potentially sensitive event contents in logs"""
		return {k: v for k, v in self.model_dump(mode='json').items() if k.startswith('event_') and 'results' not in k}

	def __await__(self) -> Self:
		"""Wait for event to complete and return self"""

		async def wait_for_handlers_to_complete_then_return_event():
			assert self.event_completed is not None
			try:
				await asyncio.wait_for(self.event_completed.wait(), timeout=self.event_timeout)
			except TimeoutError as err:
				raise RuntimeError(
					f'{self} waiting for results timed out after {self.event_timeout}s (being processed by {len(self.event_results)} handlers)'
				) from err
			return self

		return wait_for_handlers_to_complete_then_return_event().__await__()

	def __hash__(self) -> int:
		"""Make events hashable using their unique event_id"""
		return hash(self.event_id)

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
	def event_started_at(self) -> datetime | None:
		"""Timestamp when event first started being processed by any handler"""
		started_times = [result.started_at for result in self.event_results.values() if result.started_at is not None]
		# If no handlers but event was processed, use the processed timestamp
		if not started_times and self._event_processed_at:
			return self._event_processed_at
		return min(started_times) if started_times else None

	@property
	def event_completed_at(self) -> datetime | None:
		"""Timestamp when event was completed by all handlers"""
		# If no handlers at all but event was processed, use the processed timestamp
		if not self.event_results and self._event_processed_at:
			return self._event_processed_at

		# All handlers must be done (completed or error)
		all_done = all(result.status in ('completed', 'error') for result in self.event_results.values())
		if not all_done:
			return None

		# Return the latest completion time
		completed_times = [result.completed_at for result in self.event_results.values() if result.completed_at is not None]
		return max(completed_times) if completed_times else self._event_processed_at

	@property
	def event_status(self) -> str:
		return 'completed' if self.event_completed_at else 'started' if self.event_started_at else 'pending'

	def model_post_init(self, __context: Any) -> None:
		"""Append the library version number to the event schema so we know what version was used to create any JSON dump"""
		if self.event_schema is None:
			version = get_browser_use_version()
			self.event_schema = f'{self.__class__.__module__}.{self.__class__.__qualname__}@{version}'

	def event_result_update(
		self, handler: EventHandler | None = None, eventbus: 'EventBus | None' = None, **kwargs
	) -> 'EventResult':
		"""Create or update an EventResult for a handler"""
		handler_id: PythonIdStr = str(id(handler))
		eventbus = eventbus or (handler and hasattr(handler, '__self__') and handler.__self__) or None
		eventbus_id: PythonIdStr = str(id(eventbus))

		# Get or create EventResult
		if handler_id not in self.event_results:
			self.event_results[handler_id] = EventResult(
				handler_id=handler_id,
				handler_name=handler and handler.__name__ or str(handler),
				eventbus_id=eventbus_id,
				eventbus_name=eventbus and eventbus.name or str(eventbus),
				event_parent_id=self.event_id,
				status=kwargs.get('status', 'pending'),
				timeout=self.event_timeout,
			)
			logger.debug(f'Created EventResult for handler {handler_id}: {handler.__name__}')

		# Update the EventResult with provided kwargs
		self.event_results[handler_id].update(**kwargs)
		logger.debug(
			f'Updated EventResult for handler {handler_id}: status={self.event_results[handler_id].status}, total_results={len(self.event_results)}'
		)
		# Don't mark complete here - let the EventBus do it after all handlers are done
		return self.event_results[handler_id]

	def _mark_complete_if_all_handlers_completed(self) -> None:
		"""Check if all handlers are done and signal completion"""
		if self.event_completed and not self.event_completed.is_set():
			# If there are no results at all, the event is complete
			if not self.event_results:
				self._event_processed_at = datetime.now(UTC)
				self.event_completed.set()
				return

			# Otherwise check if all results are done
			all_done = all(result.status in ('completed', 'error') for result in self.event_results.values())
			if all_done:
				self._event_processed_at = datetime.now(UTC)
				self.event_completed.set()

	async def event_results_by_handler_id(self, timeout: float | None = None) -> dict[PythonIdStr, Any]:
		"""Get all results by handler id"""
		try:
			await asyncio.wait_for(self.event_completed.wait(), timeout=timeout or self.event_timeout)
		except TimeoutError:
			pass
		return {handler_id: await event_result for handler_id, event_result in self.event_results.items()}

	async def event_results_flat_dict(self, timeout: float | None = None) -> dict[Any, Any]:
		"""Merge all dict results into single dict"""

		try:
			await asyncio.wait_for(self.event_completed.wait(), timeout=timeout or self.event_timeout)
		except TimeoutError:
			pass

		merged_results: dict[Any, Any] = {}
		for event_result in self.event_results.values():
			if event_result.status == 'completed' and event_result.result is not None:
				if not event_result.result:  # skip if result is {} or None
					continue
				if isinstance(event_result.result, BaseEvent):  # skip if result is another Event
					continue
				if not isinstance(event_result.result, dict):
					# raise TypeError(f"Handler '{event_result.handler_name}' returned {type(event_result.result).__name__} instead of dict")
					continue
				merged_results.update(event_result.result)  # update the merged dict with the contents of the result dict
		return merged_results

	async def event_results_flat_list(self, timeout: float | None = None) -> list[Any]:
		"""Merge all list results into single list"""

		try:
			await asyncio.wait_for(self.event_completed.wait(), timeout=timeout or self.event_timeout)
		except TimeoutError:
			pass

		merged_results = []
		for event_result in self.event_results.values():
			if event_result.status == 'completed' and event_result.result is not None:
				if isinstance(event_result.result, list):
					merged_results.extend(event_result.result)  # append the contents of the list to the merged list
				elif isinstance(event_result.result, BaseEvent):  # skip if result is another Event
					continue
				else:
					merged_results.append(event_result.result)  # append individual item to the merged list
		return merged_results

	async def event_result(self, timeout: float | None = None) -> Any:
		results = await self.event_results_flat_list(timeout=timeout)
		return results[0] if results else None


# PSA: All BaseEvent buil-in attrs and methods must be prefixed with "event_" in order to avoid clashing with data contents (which share a namespace with the metadata)
# This is the same approach Pydantic uses for their special `model_*` attrs (and BaseEvent is also a pydantic model, so model_ prefixes are reserved too)
# resist the urge to nest the event data in an inner object unless absolutely necessary, flat simplifies most of the code and makes it easier to read JSON logs with less nesting
pydantic_builtin_attrs = dir(BaseModel)
event_builtin_attrs = {key for key in dir(BaseEvent) if key.startswith('event_')}
attr_name_allowed = lambda key: key in pydantic_builtin_attrs or key in event_builtin_attrs or key.startswith('_')
illegal_attrs = {key for key in dir(BaseEvent) if not attr_name_allowed(key)}
assert not illegal_attrs, (
	f'All BaseEvent attrs and methods must be prefixed with "event_" in order to avoid clashing with BaseEvent subclass fields used to store event contents (which share a namespace with the event_ metadata). not allowed: {illegal_attrs}'
)


class EventResult(BaseModel):
	"""Individual result from a single handler"""

	id: str = Field(default_factory=uuid7str)
	handler_id: str
	handler_name: str
	eventbus_id: str
	eventbus_name: str
	timeout: float = 60.0
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
		return f'{handler_qualname}() -> {self.result or self.error or "..."} ({self.status})'

	def __repr__(self) -> str:
		icon = '🏃' if self.status == 'pending' else '✅' if self.status == 'completed' else '❌'
		return f'{self.handler_name}#{self.handler_id[-4:]}(e) {icon}'

	def __await__(self):
		"""Wait for this result to complete and return the result or raise error"""

		async def wait_for_handler_to_complete_and_return_result():
			try:
				await asyncio.wait_for(self.completed.wait(), timeout=self.timeout)
			except TimeoutError as err:
				raise RuntimeError(f'Handler {self.handler_name} timed out after {self.event_timeout}s') from err

			if self.status == 'error' and self.error:
				raise RuntimeError(f'Handler {self.handler_name} failed: {self.error}')

			return self.result

		return wait_for_handler_to_complete_and_return_result().__await__()

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
