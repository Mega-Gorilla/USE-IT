"""Remote browser connection that connects via CDP."""

from typing import Any

from bubus import EventBus
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from pydantic import BaseModel, ConfigDict, PrivateAttr

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStartedEvent,
	BrowserStoppedEvent,
	TabsInfoRequestEvent,
	NavigationCompleteEvent,
	PageCrashedEvent,
	StartBrowserEvent,
	StopBrowserEvent,
	TabCreatedEvent,
	TabsInfoResponseEvent,
)
from browser_use.browser.profile import BrowserProfile


class RemoteBrowserConnection(BaseModel):
	"""Base connection for all browser connections via CDP.

	This class handles connections to existing browsers via Chrome DevTools Protocol (CDP).
	"""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	# Configuration
	browser_profile: BrowserProfile
	event_bus: EventBus

	# Connection URL
	cdp_url: str

	# Runtime state (private)
	_playwright: Playwright | None = PrivateAttr(default=None)
	_browser: Browser | None = PrivateAttr(default=None)
	_context: BrowserContext | None = PrivateAttr(default=None)
	_current_page: Page | None = PrivateAttr(default=None)
	_pages: list[Page] = PrivateAttr(default_factory=list)
	_tab_id_map: dict[int, str] = PrivateAttr(default_factory=dict)  # tab_index -> tab_id

	# State tracking
	_started: bool = PrivateAttr(default=False)

	def __init__(self, **data):
		"""Initialize and register event handlers."""
		super().__init__(**data)
		self._register_handlers()

	def _register_handlers(self) -> None:
		"""Register event handlers for browser control events."""
		self.event_bus.on(StartBrowserEvent, self._handle_start)
		self.event_bus.on(StopBrowserEvent, self._handle_stop)
		self.event_bus.on(TabsInfoRequestEvent, self._handle_tabs_info_request)

	async def _handle_start(self, event: StartBrowserEvent) -> None:
		"""Handle browser start request."""
		if self._started:
			self.event_bus.dispatch(
				BrowserStartedEvent(
					cdp_url=self.cdp_url,
					browser_pid=None,
				)
			)
			return

		try:
			self._playwright = await async_playwright().start()

			# Connect via CDP
			await self._connect_via_cdp()

			# Set up the browser context
			await self._setup_browser_context()

			self._started = True

			# Notify that browser has started
			self.event_bus.dispatch(
				BrowserStartedEvent(
					cdp_url=self.cdp_url,
					browser_pid=None,
				)
			)

		except Exception as e:
			# Clean up on failure
			await self._cleanup_playwright()

			# Notify error
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='ConnectionFailed',
					message=f'Failed to connect to browser: {str(e)}',
					details={'cdp_url': self.cdp_url},
				)
			)
			raise

	async def _connect_via_cdp(self) -> None:
		"""Connect to browser via Chrome DevTools Protocol."""
		# All channels (chrome, chromium, msedge, etc.) use chromium in playwright
		browser_type = self._playwright.chromium

		# CDP connection args from profile
		connection_args = {
			'endpoint_url': self.cdp_url,
			**self.browser_profile.kwargs_for_cdp_connection(),
		}

		self._browser = await browser_type.connect_over_cdp(**connection_args)

	async def _setup_browser_context(self) -> None:
		"""Set up the browser context with profile settings."""
		if not self._browser:
			raise RuntimeError('Browser not connected')

		# Get or create context
		contexts = self._browser.contexts
		if contexts:
			self._context = contexts[0]
		else:
			# Create new context with profile settings
			context_args = self.browser_profile.kwargs_for_new_context().model_dump(exclude_none=True)
			self._context = await self._browser.new_context(**context_args)

		# Track existing pages
		self._pages = list(self._context.pages)
		if self._pages:
			self._current_page = self._pages[0]

	async def _handle_stop(self, event: StopBrowserEvent) -> None:
		"""Handle browser stop request."""
		if not self._started:
			self.event_bus.dispatch(
				BrowserStoppedEvent(
					reason='Browser was not started',
				)
			)
			return

		try:
			# Close context if we created it
			if self._context and not self._context.pages:
				await self._context.close()

			# CDP connections should be disconnected, not closed
			# The browser instance will be cleaned up when playwright stops

			# Clean up playwright
			await self._cleanup_playwright()

			# Reset state
			self._browser = None
			self._context = None
			self._current_page = None
			self._pages.clear()
			self._tab_id_map.clear()
			self._started = False

			# Notify that browser has stopped
			self.event_bus.dispatch(
				BrowserStoppedEvent(
					reason='Stopped by request',
				)
			)

		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='StopFailed',
					message=f'Failed to stop browser: {str(e)}',
				)
			)

	async def _cleanup_playwright(self) -> None:
		"""Clean up playwright instance."""
		if self._playwright:
			await self._playwright.stop()
			self._playwright = None

	async def _handle_tabs_info_request(self, event: TabsInfoRequestEvent) -> None:
		"""Handle request for tabs information."""
		if not self._started or not self._context:
			self.event_bus.dispatch(TabsInfoResponseEvent(tabs=[]))
			return

		tabs = []
		for i, page in enumerate(self._pages):
			if not page.is_closed():
				tab_id = self._tab_id_map.get(i, f'tab_{i}')
				tabs.append(
					{
						'id': tab_id,
						'index': i,
						'url': page.url,
						'title': await page.title(),
					}
				)

		self.event_bus.dispatch(TabsInfoResponseEvent(tabs=tabs))

	# Page management methods (used internally by session)
	async def navigate_page(self, tab_index: int, url: str, wait_until: str = 'load') -> None:
		"""Navigate a specific tab to a URL."""
		page = await self._get_page_by_index(tab_index)
		if not page:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='InvalidTab',
					message=f'Tab {tab_index} not found',
				)
			)
			return

		try:
			response = await page.goto(url, wait_until=wait_until)
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=tab_index,
					url=url,
					status=response.status if response else None,
				)
			)
		except Exception as e:
			if 'crash' in str(e).lower():
				self.event_bus.dispatch(
					PageCrashedEvent(
						tab_index=tab_index,
						error=str(e),
					)
				)
			else:
				self.event_bus.dispatch(
					BrowserErrorEvent(
						error_type='NavigationFailed',
						message=f'Failed to navigate: {str(e)}',
						details={'url': url, 'tab_index': tab_index},
					)
				)

	async def create_tab(self, url: str | None = None) -> None:
		"""Create a new tab."""
		if not self._context:
			return

		page = await self._context.new_page()
		tab_index = len(self._pages)
		self._pages.append(page)

		# Generate tab ID
		tab_id = f'tab_{tab_index}_{id(page)}'
		self._tab_id_map[tab_index] = tab_id

		# Set up page event handlers
		page.on(
			'crash',
			lambda: self.event_bus.dispatch(
				PageCrashedEvent(
					tab_index=tab_index,
					error='Page crashed',
				)
			),
		)

		if url:
			await page.goto(url)

		self.event_bus.dispatch(
			TabCreatedEvent(
				tab_id=tab_id,
				tab_index=tab_index,
				url=url,
			)
		)

	async def _get_page_by_index(self, tab_index: int) -> Page | None:
		"""Get page by tab index."""
		if 0 <= tab_index < len(self._pages):
			page = self._pages[tab_index]
			if not page.is_closed():
				return page
		return None

	async def click_element(self, tab_index: int, element_index: int, **kwargs) -> None:
		"""Click an element on a page."""
		page = await self._get_page_by_index(tab_index)
		if not page:
			return

		# This would need DOM element tracking implementation
		# For now, just show the pattern
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='NotImplemented',
				message='Element clicking needs DOM tracking implementation',
			)
		)

	async def input_text(self, tab_index: int, element_index: int, text: str, **kwargs) -> None:
		"""Input text into an element."""
		page = await self._get_page_by_index(tab_index)
		if not page:
			return

		# This would need DOM element tracking implementation
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='NotImplemented',
				message='Text input needs DOM tracking implementation',
			)
		)

	async def take_screenshot(self, tab_index: int, full_page: bool = False, clip: dict | None = None) -> str:
		"""Take a screenshot of a page."""
		page = await self._get_page_by_index(tab_index)
		if not page:
			return ''

		screenshot_bytes = await page.screenshot(
			full_page=full_page,
			clip=clip,
		)

		# Convert to base64
		import base64

		return base64.b64encode(screenshot_bytes).decode('utf-8')

	async def get_browser_state(self, tab_index: int, include_dom: bool = True) -> dict[str, Any]:
		"""Get the current state of a browser tab."""
		page = await self._get_page_by_index(tab_index)
		if not page:
			return {'error': 'Tab not found'}

		state = {
			'url': page.url,
			'title': await page.title(),
			'viewport': page.viewport_size,
		}

		if include_dom:
			# This would need DOM extraction implementation
			state['dom'] = 'DOM extraction not implemented'

		return state

	@property
	def browser(self) -> Browser | None:
		"""Get the connected browser instance."""
		return self._browser

	@property
	def context(self) -> BrowserContext | None:
		"""Get the browser context."""
		return self._context

	@property
	def is_connected(self) -> bool:
		"""Check if connected to a browser."""
		return self._browser is not None and self._browser.is_connected()
