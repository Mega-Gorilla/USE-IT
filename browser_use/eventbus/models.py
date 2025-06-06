import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, PrivateAttr
from uuid_extensions import uuid7str

from browser_use.utils import get_browser_use_version

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

	event_schema: str | None = Field(default=None, description='Event schema version in format ClassName@version', max_length=100)
	event_type: str
	event_id: str = Field(default_factory=uuid7str)
	event_path: list[str] = Field(default_factory=list, description='Path tracking for event routing')
	queued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

	# Completion tracking fields
	started_at: datetime | None = Field(default=None)
	completed_at: datetime | None = Field(default=None)
	results: dict[str, Any] = Field(default_factory=dict)  # {handler_name: result}
	errors: dict[str, str] = Field(default_factory=dict)  # {handler_name: error_str}
	_completion_event: asyncio.Event | None = PrivateAttr(default=None)

	@property
	def state(self) -> str:
		return 'completed' if self.completed_at else 'started' if self.started_at else 'queued'

	async def result(self):
		"""Wait for completion and return self with results"""
		if self._completion_event:
			await self._completion_event.wait()
		return self

	def record_results(self, results: dict[str, Any] | None = None, complete: bool = True) -> None:
		"""Update the event results and optionally mark it as completed"""
		self.results = {
			**(self.results or {}),
			**(results or {}),
		}
		if complete:
			self.completed_at = datetime.now(UTC)
			if self._completion_event:
				self._completion_event.set()

	def model_post_init(self, __context: Any) -> None:
		"""Initialize completion event and set event schema after model creation"""

		if self.event_schema is None:
			version = get_browser_use_version()
			self.event_schema = f'{self.__class__.__module__}.{self.__class__.__qualname__}@{version}'

		try:
			asyncio.get_running_loop()  # Only create event if we're in an async context
			self._completion_event = asyncio.Event()
		except RuntimeError:
			self._completion_event = None  # Not in async context, skip
