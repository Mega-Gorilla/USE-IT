"""Base watchdog class for browser monitoring components."""

import inspect
from typing import TYPE_CHECKING, ClassVar

from bubus import BaseEvent, EventBus
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict

from browser_use.utils import logger

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession


class BaseWatchdog(BaseModel):
	"""Base class for all browser watchdogs.

	Watchdogs monitor browser state and emit events based on changes.
	They automatically register event handlers based on method names.

	Handler methods should be named: on_EventTypeName(self, event: EventTypeName)
	"""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	# Class variables defining event contracts (optional, for documentation)
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = []  # Events this watchdog listens to
	EMITS: ClassVar[list[type[BaseEvent]]] = []  # Events this watchdog emits

	# Core dependencies
	event_bus: EventBus
	browser_session: 'BrowserSession'

	async def attach_to_session(self) -> None:
		"""Attach watchdog to its browser session and start monitoring.

		This method handles event listener registration. The watchdog is already
		bound to a browser session via self.browser_session from initialization.
		"""
		# Register event handlers automatically based on method names
		from browser_use.browser import events

		event_classes = {}
		for name in dir(events):
			obj = getattr(events, name)
			if inspect.isclass(obj) and issubclass(obj, BaseEvent) and obj is not BaseEvent:
				event_classes[name] = obj

		# Find all handler methods (on_EventName)
		registered_events = set()
		for method_name in dir(self):
			if method_name.startswith('on_') and callable(getattr(self, method_name)):
				# Extract event name from method name (on_EventName -> EventName)
				event_name = method_name[3:]  # Remove 'on_' prefix

				if event_name in event_classes:
					event_class = event_classes[event_name]

					# ASSERTION: If LISTENS_TO is defined, enforce it
					if self.LISTENS_TO:
						assert event_class in self.LISTENS_TO, (
							f'[{self.__class__.__name__}] Handler {method_name} listens to {event_name} '
							f'but {event_name} is not declared in LISTENS_TO: {[e.__name__ for e in self.LISTENS_TO]}'
						)

					handler = getattr(self, method_name)
					self.event_bus.on(event_class, handler)
					registered_events.add(event_class)

		# ASSERTION: If LISTENS_TO is defined, ensure all declared events have handlers
		if self.LISTENS_TO:
			missing_handlers = set(self.LISTENS_TO) - registered_events
			if missing_handlers:
				missing_names = [e.__name__ for e in missing_handlers]
				logger.warning(
					f'[{self.__class__.__name__}] LISTENS_TO declares {missing_names} '
					f'but no handlers found (missing on_{"_, on_".join(missing_names)} methods)'
				)

	async def attach_to_page(self, page: Page) -> None:
		"""Set up monitoring for a specific page. Override in subclasses.

		This method should be idempotent - safe to call multiple times on the same page.
		"""
		pass

	def safe_execute(self, func, *args, **kwargs):
		"""Execute function with automatic error logging."""
		try:
			return func(*args, **kwargs)
		except Exception as e:
			logger.error(f'[{self.__class__.__name__}] Error in {func.__name__}: {e}')
			return None


# Fix Pydantic circular dependency handling - subclasses should call this after BrowserSession is defined
