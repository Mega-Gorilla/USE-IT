import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr
from uuid_extensions import uuid7str

from browser_use.utils import get_browser_use_version

# Constants for validation
MAX_STRING_LENGTH = 10000  # 10K chars for most strings
MAX_URL_LENGTH = 2000
MAX_TASK_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000
MAX_FILE_CONTENT_SIZE = 50 * 1024 * 1024  # 50MB


class BaseEvent(BaseModel):
	"""
	The base model used for all Events that flow through the EventBus system.
	"""

	event_schema: str | None = Field(default=None, description='Event schema version in format ClassName@version', max_length=100)
	event_type: str
	event_id: str = Field(default_factory=uuid7str)
	queued_at: datetime = Field(default_factory=datetime.utcnow)
	event_path: list[str] = Field(default_factory=list, description='Path tracking for event routing')

	# Completion tracking fields
	started_at: datetime | None = Field(default=None)
	completed_at: datetime | None = Field(default=None)
	results: dict[str, Any] = Field(default_factory=dict)
	errors: dict[str, str] = Field(default_factory=dict)  # Store error messages as strings

	# Private field for completion tracking
	_completion_event: asyncio.Event | None = PrivateAttr(default=None)

	async def result(self):
		"""Wait for completion and return self with results"""
		await self.wait_for_completion()
		return self

	@property
	def state(self) -> str:
		"""Return current event state: 'queued' | 'started' | 'completed'"""
		if self.completed_at:
			return 'completed'
		elif self.started_at:
			return 'started'
		return 'queued'

	@property
	def duration(self) -> float | None:
		"""Return processing duration in seconds, or None if not completed"""
		if self.started_at:
			return ((self.completed_at or datetime.now(UTC)) - self.started_at).total_seconds()
		return None

	def model_post_init(self, __context: Any) -> None:
		"""Initialize completion event and set event schema after model creation"""
		# Set event_schema if not already set
		if self.event_schema is None:
			version = get_browser_use_version()
			class_module = self.__class__.__module__
			class_qualname = self.__class__.__qualname__
			self.event_schema = f'{class_module}.{class_qualname}@{version}'

		try:
			# Only create event if we're in an async context
			asyncio.get_running_loop()
			self._completion_event = asyncio.Event()
		except RuntimeError:
			# Not in async context, skip
			self._completion_event = None

	async def wait_for_completion(self) -> None:
		"""Wait for this event to be fully processed"""
		if self._completion_event:
			await self._completion_event.wait()

	def mark_completed(self) -> None:
		"""Mark this event as completed"""
		self.completed_at = datetime.now(UTC)
		if self._completion_event:
			self._completion_event.set()
