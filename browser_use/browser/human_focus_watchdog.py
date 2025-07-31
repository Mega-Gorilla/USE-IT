"""Human focus watchdog for tracking which tab the human is viewing."""

import asyncio
from typing import Any
from weakref import WeakKeyDictionary

from bubus import EventBus
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.events import (
	BrowserStartedEvent,
	BrowserStoppedEvent,
	HumanFocusChangedEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from browser_use.utils import logger


class HumanFocusWatchdog(BaseModel):
	"""Tracks which tab the human is currently viewing/focused on."""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	event_bus: EventBus
	browser_session: Any  # Avoid circular import

	# Use WeakKeyDictionary to avoid holding references to closed pages
	_page_visibility_handlers: WeakKeyDictionary[Page, Any] = PrivateAttr(default_factory=WeakKeyDictionary)
	_current_human_page: Page | None = PrivateAttr(default=None)
	_monitoring_task: asyncio.Task | None = PrivateAttr(default=None)

	def __init__(self, event_bus: EventBus, browser_session: Any, **kwargs):
		"""Initialize watchdog with event bus and browser session."""
		super().__init__(event_bus=event_bus, browser_session=browser_session, **kwargs)
		self._register_handlers()

	def _register_handlers(self) -> None:
		"""Register event handlers."""
		self.event_bus.on(BrowserStartedEvent, self._handle_browser_started)
		self.event_bus.on(BrowserStoppedEvent, self._handle_browser_stopped)
		self.event_bus.on(TabCreatedEvent, self._handle_tab_created)
		self.event_bus.on(TabClosedEvent, self._handle_tab_closed)
		self.event_bus.on(SwitchTabEvent, self._handle_switch_tab)

	async def _handle_browser_started(self, event: BrowserStartedEvent) -> None:
		"""Start monitoring when browser starts."""
		logger.info('[HumanFocusWatchdog] Browser started')
		await self._setup_initial_pages()

	async def _handle_browser_stopped(self, event: BrowserStoppedEvent) -> None:
		"""Clean up when browser stops."""
		logger.info('[HumanFocusWatchdog] Browser stopped')
		self._current_human_page = None
		self._page_visibility_handlers.clear()

	async def _handle_tab_created(self, event: TabCreatedEvent) -> None:
		"""Monitor new tabs for visibility changes."""
		# The actual page will be set up when we detect it via browser context
		logger.debug(f'[HumanFocusWatchdog] Tab created: {event.url}')
		# Small delay to let tab finish loading
		await asyncio.sleep(0.5)
		await self._setup_new_pages()

	async def _handle_tab_closed(self, event: TabClosedEvent) -> None:
		"""Handle tab closure."""
		logger.debug(f'[HumanFocusWatchdog] Tab closed at index {event.tab_index}')
		# If the closed tab was the human's current page, find a new one
		if self._current_human_page and self._current_human_page.is_closed():
			await self._find_new_human_page()

	async def _handle_switch_tab(self, event: SwitchTabEvent) -> None:
		"""Handle programmatic tab switches."""
		# This could be the agent switching tabs, not necessarily the human
		# We rely on visibility API to track actual human focus
		pass

	@property
	def current_human_page(self) -> Page | None:
		"""Get the current page the human is viewing."""
		return self._current_human_page

	async def _setup_initial_pages(self) -> None:
		"""Set up visibility tracking for initial pages."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			return

		pages = self.browser_session._browser_context.pages
		for page in pages:
			await self._setup_page_visibility_tracking(page)

		# Set initial human page to first page if not set
		if not self._current_human_page and pages:
			self._current_human_page = pages[0]
			self._dispatch_focus_changed()

	async def _setup_new_pages(self) -> None:
		"""Check for new pages and set up visibility tracking."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			return

		pages = self.browser_session._browser_context.pages
		for page in pages:
			if page not in self._page_visibility_handlers:
				await self._setup_page_visibility_tracking(page)

	async def _setup_page_visibility_tracking(self, page: Page) -> None:
		"""Set up visibility tracking for a page."""
		try:
			# Skip new tab pages as they can hang when evaluating scripts
			if page.url in ['about:blank', 'chrome://new-tab-page/', 'chrome://newtab/']:
				return

			# Define the visibility change handler
			visibility_script = """
				() => {
					// Check if we've already set up the listener
					if (window.__humanFocusListenerSetup) return;
					window.__humanFocusListenerSetup = true;

					// Function to report visibility
					const reportVisibility = () => {
						if (document.visibilityState === 'visible') {
							// This tab became visible - human is looking at it
							window.__reportHumanFocus && window.__reportHumanFocus();
						}
					};

					// Listen for visibility changes
					document.addEventListener('visibilitychange', reportVisibility);
					
					// Also listen for focus events
					window.addEventListener('focus', reportVisibility);
					
					// Report initial state if visible
					if (document.visibilityState === 'visible') {
						reportVisibility();
					}
				}
			"""

			# Expose a function that the page can call when it becomes visible
			await page.expose_function('__reportHumanFocus', lambda: self._handle_page_focus(page))
			
			# Inject the visibility tracking script
			await page.evaluate(visibility_script)
			
			# Store that we've set up tracking for this page
			self._page_visibility_handlers[page] = True
			
			logger.debug(f'[HumanFocusWatchdog] Set up visibility tracking for {page.url}')

		except Exception as e:
			logger.debug(f'[HumanFocusWatchdog] Failed to set up visibility tracking for {page.url}: {e}')

	def _handle_page_focus(self, page: Page) -> None:
		"""Handle when a page reports it has focus."""
		if self._current_human_page != page:
			logger.info(f'[HumanFocusWatchdog] Human focus changed to: {page.url}')
			self._current_human_page = page
			self._dispatch_focus_changed()

	def _dispatch_focus_changed(self) -> None:
		"""Dispatch event when human focus changes."""
		if self._current_human_page:
			try:
				# Find the tab index
				pages = self.browser_session._browser_context.pages
				tab_index = pages.index(self._current_human_page) if self._current_human_page in pages else -1
				
				self.event_bus.dispatch(
					HumanFocusChangedEvent(
						tab_index=tab_index,
						url=self._current_human_page.url,
					)
				)
			except Exception as e:
				logger.error(f'[HumanFocusWatchdog] Error dispatching focus change: {e}')

	async def _find_new_human_page(self) -> None:
		"""Find a new page for human focus when current one is closed."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			self._current_human_page = None
			return

		pages = self.browser_session._browser_context.pages
		if pages:
			# Pick the first available page
			self._current_human_page = pages[0]
			self._dispatch_focus_changed()
			page_url = self._current_human_page.url if self._current_human_page else 'unknown'
			logger.info(f'[HumanFocusWatchdog] Switched human focus to: {page_url}')
		else:
			self._current_human_page = None