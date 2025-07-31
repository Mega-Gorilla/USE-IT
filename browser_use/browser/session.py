"""Event-driven browser session with backwards compatibility."""

import asyncio
import base64
import warnings
from typing import TYPE_CHECKING, Any, Self

from bubus import EventBus
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStartedEvent,
	BrowserStateRequestEvent,
	BrowserStateResponseEvent,
	BrowserStoppedEvent,
	ClickElementEvent,
	CloseTabEvent,
	CreateTabEvent,
	InputTextEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	ScreenshotRequestEvent,
	ScreenshotResponseEvent,
	ScrollEvent,
	StartBrowserEvent,
	StopBrowserEvent,
	SwitchTabEvent,
	TabCreatedEvent,
	TabsInfoRequestEvent,
	TabsInfoResponseEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.utils import logger

if TYPE_CHECKING:
	pass

# Default browser profile for convenience
DEFAULT_BROWSER_PROFILE = BrowserProfile()


class BrowserSession(BaseModel):
	"""Event-driven browser session with backwards compatibility.

	This class provides a 2-layer architecture:
	- High-level event handling for agents/controllers
	- Direct CDP/Playwright calls for browser operations

	Supports both event-driven and imperative calling styles.
	"""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	# Core configuration
	browser_profile: BrowserProfile
	id: str = Field(default_factory=lambda: uuid7str())

	# Connection info (for backwards compatibility)
	cdp_url: str | None = None
	is_local: bool = Field(default=True)

	# Event bus
	event_bus: EventBus = Field(default_factory=EventBus)

	# Browser state
	_playwright: Playwright | None = PrivateAttr(default=None)
	_browser: Browser | None = PrivateAttr(default=None)
	_browser_context: BrowserContext | None = PrivateAttr(default=None)
	_current_agent_page: Page | None = PrivateAttr(default=None)
	_current_human_page: Page | None = PrivateAttr(default=None)

	# Local browser state (only used when cdp_url is None)
	_subprocess: Any = PrivateAttr(default=None)  # psutil.Process
	_owns_browser_resources: bool = PrivateAttr(default=True)

	def __init__(
		self,
		browser_profile: BrowserProfile,
		cdp_url: str | None = None,
		wss_url: str | None = None,
		browser_pid: int | None = None,
		**kwargs: Any,
	):
		"""Initialize a browser session.

		Args:
			browser_profile: Browser configuration profile
			cdp_url: CDP URL for connecting to existing browser
			wss_url: WSS URL for connecting to remote playwright (DEPRECATED)
			browser_pid: Process ID of existing browser (DEPRECATED)
			**kwargs: Additional arguments
		"""
		# Handle deprecated parameters
		if wss_url:
			raise ValueError(
				'wss_url is no longer supported. Browser Use now only supports CDP connections. Please use cdp_url instead.'
			)

		# Initialize base model
		super().__init__(
			browser_profile=browser_profile,
			cdp_url=cdp_url,
			**kwargs,
		)

		# Create event bus with unique name
		self.event_bus = EventBus(name=f'BrowserSession_{self.id[-4:]}')

		# Set is_local based on cdp_url
		self.is_local = cdp_url is None

		# Handle deprecated browser_pid
		if browser_pid is not None:
			warnings.warn(
				'Passing browser_pid to BrowserSession is deprecated. Use from_existing_pid() class method instead.',
				DeprecationWarning,
				stacklevel=2,
			)
			if not cdp_url:
				raise ValueError('cdp_url is required when browser_pid is provided')

			# Convert PID to psutil.Process
			try:
				import psutil

				self._subprocess = psutil.Process(browser_pid)
				self._owns_browser_resources = False
			except ImportError:
				raise ImportError('psutil is required for process management')
		else:
			# Set ownership based on whether we're connecting to existing browser
			self._owns_browser_resources = cdp_url is None

		# Register event handlers
		self._register_handlers()

	@classmethod
	def from_existing_pid(
		cls,
		browser_profile: BrowserProfile,
		pid: int,
		cdp_url: str,
		**kwargs: Any,
	) -> Self:
		"""Create a session from an existing browser process.

		Args:
			browser_profile: Browser configuration profile
			pid: Process ID of the existing browser
			cdp_url: CDP URL to connect to the browser
			**kwargs: Additional arguments
		"""
		session = cls(
			browser_profile=browser_profile,
			cdp_url=cdp_url,
			**kwargs,
		)
		# Convert PID to psutil.Process
		try:
			import psutil

			session._subprocess = psutil.Process(pid)
			session._owns_browser_resources = False
		except ImportError:
			raise ImportError('psutil is required for process management')
		return session

	def _register_handlers(self) -> None:
		"""Register event handlers for browser control."""
		# Browser lifecycle
		self.event_bus.on(StartBrowserEvent, self._handle_start)
		self.event_bus.on(StopBrowserEvent, self._handle_stop)

		# Navigation and interaction
		self.event_bus.on(NavigateToUrlEvent, self._handle_navigate)
		self.event_bus.on(ClickElementEvent, self._handle_click)
		self.event_bus.on(InputTextEvent, self._handle_input_text)
		self.event_bus.on(ScrollEvent, self._handle_scroll)

		# Tab management
		self.event_bus.on(SwitchTabEvent, self._handle_switch_tab)
		self.event_bus.on(CreateTabEvent, self._handle_create_tab)
		self.event_bus.on(CloseTabEvent, self._handle_close_tab)

		# Browser state
		self.event_bus.on(BrowserStateRequestEvent, self._handle_browser_state_request)
		self.event_bus.on(ScreenshotRequestEvent, self._handle_screenshot_request)
		self.event_bus.on(TabsInfoRequestEvent, self._handle_tabs_info_request)

	# ========== Event Handlers ==========

	async def _handle_start(self, event: StartBrowserEvent) -> None:
		"""Handle browser start request."""
		if self._browser and self._browser.is_connected():
			# Already started
			self.event_bus.dispatch(
				BrowserStartedEvent(
					cdp_url=self.cdp_url,
					browser_pid=self._subprocess.pid if self._subprocess else None,
				)
			)
			return

		try:
			if self.is_local and not self.cdp_url:
				# Launch local browser
				from browser_use.browser.local import LocalBrowserHelpers

				self._subprocess, self.cdp_url = await LocalBrowserHelpers.launch_browser(self.browser_profile)

			# Connect via CDP
			self._playwright = await async_playwright().start()
			self._browser = await self._playwright.chromium.connect_over_cdp(
				self.cdp_url,
				**self.browser_profile.kwargs_for_cdp_connection(),
			)

			# Set up browser context
			contexts = self._browser.contexts
			if contexts:
				self._browser_context = contexts[0]
			else:
				self._browser_context = await self._browser.new_context(
					**self.browser_profile.kwargs_for_new_context().model_dump(exclude_none=True)
				)

			# Set initial page if exists
			pages = self._browser_context.pages
			if pages:
				self._current_agent_page = pages[0]
				self._current_human_page = pages[0]

			# Notify success
			self.event_bus.dispatch(
				BrowserStartedEvent(
					cdp_url=self.cdp_url,
					browser_pid=self._subprocess.pid if self._subprocess else None,
				)
			)

		except Exception as e:
			# Clean up on failure
			if self._playwright:
				await self._playwright.stop()
				self._playwright = None

			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='StartFailed',
					message=f'Failed to start browser: {str(e)}',
					details={'cdp_url': self.cdp_url},
				)
			)
			raise

	async def _handle_stop(self, event: StopBrowserEvent) -> None:
		"""Handle browser stop request."""
		if not self._browser:
			self.event_bus.dispatch(
				BrowserStoppedEvent(
					reason='Browser was not started',
				)
			)
			return

		try:
			# Check if we should keep the browser alive
			if self.browser_profile.keep_alive and not event.force:
				# Keep browser alive, just notify stop
				self.event_bus.dispatch(
					BrowserStoppedEvent(
						reason='Kept alive due to keep_alive=True',
					)
				)
				return

			# Close context if we created it
			if self._browser_context and not self._browser_context.pages:
				await self._browser_context.close()

			# Clean up playwright
			if self._playwright:
				await self._playwright.stop()
				self._playwright = None

			# Stop local browser process if we own it
			if self.is_local and self._owns_browser_resources and self._subprocess:
				from browser_use.browser.local import LocalBrowserHelpers

				await LocalBrowserHelpers.cleanup_process(self._subprocess)

			# Reset state
			self._browser = None
			self._browser_context = None
			self._current_agent_page = None
			self._current_human_page = None

			# Clear CDP URL for local browsers since the process is gone
			if self.is_local and self._owns_browser_resources:
				self.cdp_url = None

			# Notify stop
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

	# ========== Backwards Compatibility Methods ==========
	# These all just dispatch events internally

	async def start(self) -> Self:
		"""Start the browser session."""
		event = self.event_bus.dispatch(StartBrowserEvent())
		# Wait for event to complete
		await event

		# Check if any handler had an error
		for event_result in event.event_results.values():
			if event_result.status == 'error' and event_result.error:
				raise event_result.error

		return self

	async def stop(self) -> None:
		"""Stop the browser session."""
		event = self.event_bus.dispatch(StopBrowserEvent())
		await event

	async def _handle_navigate(self, event: NavigateToUrlEvent) -> None:
		"""Handle navigation request."""
		if not self._current_agent_page:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NoActivePage',
					message='No active page to navigate',
				)
			)
			return

		try:
			response = await self._current_agent_page.goto(
				event.url,
				wait_until=event.wait_until,
			)
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=self.tabs.index(self._current_agent_page),
					url=event.url,
					status=response.status if response else None,
				)
			)
		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NavigationFailed',
					message=str(e),
					details={'url': event.url},
				)
			)

	async def _handle_click(self, event: ClickElementEvent) -> None:
		"""Handle click request."""
		# TODO: Implement DOM element tracking
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='NotImplemented',
				message='Click handling needs DOM element tracking implementation',
			)
		)

	async def _handle_input_text(self, event: InputTextEvent) -> None:
		"""Handle text input request."""
		# TODO: Implement DOM element tracking
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='NotImplemented',
				message='Text input needs DOM element tracking implementation',
			)
		)

	async def _handle_scroll(self, event: ScrollEvent) -> None:
		"""Handle scroll request."""
		# TODO: Implement scrolling
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='NotImplemented',
				message='Scrolling needs implementation',
			)
		)

	async def _handle_switch_tab(self, event: SwitchTabEvent) -> None:
		"""Handle tab switch request."""
		pages = self.tabs
		if 0 <= event.tab_index < len(pages):
			self._current_agent_page = pages[event.tab_index]

	async def _handle_create_tab(self, event: CreateTabEvent) -> None:
		"""Handle new tab creation."""
		if not self._browser_context:
			return

		page = await self._browser_context.new_page()
		if event.url:
			await page.goto(event.url)

		tab_index = len(self.tabs) - 1
		self.event_bus.dispatch(
			TabCreatedEvent(
				tab_id=f'tab_{tab_index}_{id(page)}',
				tab_index=tab_index,
				url=event.url,
			)
		)

	async def _handle_close_tab(self, event: CloseTabEvent) -> None:
		"""Handle tab close request."""
		pages = self.tabs
		if 0 <= event.tab_index < len(pages):
			await pages[event.tab_index].close()

	async def _handle_browser_state_request(self, event: BrowserStateRequestEvent) -> None:
		"""Handle browser state request."""
		try:
			# Get the full browser state with recovery
			state = await self._get_browser_state_with_recovery(
				cache_clickable_elements_hashes=event.cache_clickable_elements_hashes, include_screenshot=event.include_screenshot
			)
			self.event_bus.dispatch(BrowserStateResponseEvent(state=state))
		except Exception as e:
			# Fall back to minimal state on error
			minimal_state = await self._get_minimal_state_summary()
			self.event_bus.dispatch(BrowserStateResponseEvent(state=minimal_state))

	async def _handle_screenshot_request(self, event: ScreenshotRequestEvent) -> None:
		"""Handle screenshot request."""
		if not self._current_agent_page:
			return

		screenshot_bytes = await self._current_agent_page.screenshot(
			full_page=event.full_page,
			clip=event.clip,
		)
		screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
		self.event_bus.dispatch(ScreenshotResponseEvent(screenshot=screenshot_b64))

	async def _handle_tabs_info_request(self, event: TabsInfoRequestEvent) -> None:
		"""Handle tabs info request."""
		# Auto-start if not initialized
		if not self.initialized:
			start_event = self.event_bus.dispatch(StartBrowserEvent())
			await start_event

		tabs = []
		for i, page in enumerate(self.tabs):
			if not page.is_closed():
				tabs.append(
					{
						'id': f'tab_{i}',
						'index': i,
						'url': page.url,
						'title': await page.title(),
					}
				)
		# Dispatch the response event
		self.event_bus.dispatch(TabsInfoResponseEvent(tabs=tabs))

	# ========== Backwards Compatibility Methods ==========

	async def kill(self) -> None:
		"""Alias for stop() for backwards compatibility."""
		await self.stop()

	async def close(self) -> None:
		"""Alias for stop() for backwards compatibility."""
		await self.stop()

	async def __aenter__(self) -> Self:
		"""Async context manager entry."""
		return await self.start()

	async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
		"""Async context manager exit."""
		await self.stop()

	# Browser properties
	@property
	def initialized(self) -> bool:
		"""Check if the browser session is initialized."""
		return self._browser is not None and self._browser.is_connected()

	@property
	def browser_pid(self) -> int | None:
		"""Get the browser process ID."""
		if self._subprocess:
			return self._subprocess.pid
		return None

	@property
	def browser(self) -> Browser | None:
		"""Get the browser instance."""
		return self._browser

	@property
	def browser_context(self) -> BrowserContext | None:
		"""Get the browser context."""
		return self._browser_context

	@property
	def agent_current_page(self) -> Page | None:
		"""Get the agent's current page."""
		return self._current_agent_page

	@property
	def downloaded_files(self) -> list[str]:
		"""Get list of downloaded files."""
		# TODO: Implement download tracking
		return []

	@property
	def tabs(self) -> list[Page]:
		"""Get all open tabs/pages."""
		if self._browser_context:
			return self._browser_context.pages
		return []

	# Page management
	async def get_current_page(self) -> Page:
		"""Get the current active page."""
		if not self._current_agent_page and self.tabs:
			self._current_agent_page = self.tabs[0]
		return self._current_agent_page

	async def new_page(self, url: str | None = None) -> Page:
		"""Create a new page."""
		event = self.event_bus.dispatch(CreateTabEvent(url=url))
		await event
		return self.tabs[-1]  # Return the newly created page

	async def create_new_tab(self, url: str | None = None) -> Page:
		"""Create a new tab."""
		return await self.new_page(url)

	async def switch_to_tab(self, tab_index: int) -> None:
		"""Switch to a tab by index."""
		event = self.event_bus.dispatch(SwitchTabEvent(tab_index=tab_index))
		await event

	async def navigate_to(self, url: str) -> None:
		"""Navigate the current page to a URL."""
		event = self.event_bus.dispatch(NavigateToUrlEvent(url=url))
		await event

	async def navigate(self, url: str) -> None:
		"""Alias for navigate_to for backwards compatibility."""
		await self.navigate_to(url)

	async def go_to_url(self, url: str) -> None:
		"""Alias for navigate_to."""
		await self.navigate_to(url)

	async def go_back(self) -> None:
		"""Go back in the browser history."""
		if self._current_agent_page:
			await self._current_agent_page.go_back()

	async def go_forward(self) -> None:
		"""Go forward in the browser history."""
		if self._current_agent_page:
			await self._current_agent_page.go_forward()

	async def refresh(self) -> None:
		"""Refresh the current page."""
		if self._current_agent_page:
			await self._current_agent_page.reload()

	async def take_screenshot(self, full_page: bool = False, clip: dict | None = None) -> bytes:
		"""Take a screenshot."""
		# Dispatch the request event
		self.event_bus.dispatch(ScreenshotRequestEvent(full_page=full_page, clip=clip))
		# Wait for the response event
		try:
			response = await self.event_bus.expect(ScreenshotResponseEvent, timeout=10.0)
			return base64.b64decode(response.screenshot)
		except TimeoutError:
			# No screenshot received
			return b''

	async def click_element(self, index: int) -> None:
		"""Click element by index."""
		event = self.event_bus.dispatch(ClickElementEvent(index=index))
		await event

	async def input_text(self, index: int, text: str) -> None:
		"""Input text into element."""
		event = self.event_bus.dispatch(InputTextEvent(index=index, text=text))
		await event

	async def scroll(self, direction: str, amount: int) -> None:
		"""Scroll the page."""
		event = self.event_bus.dispatch(ScrollEvent(direction=direction, amount=amount))
		await event

	# Model copy support
	def model_copy(self, **kwargs) -> Self:
		"""Create a copy of this session."""
		# Create a new instance sharing the same browser resources
		copy = self.__class__(
			browser_profile=self.browser_profile,
			cdp_url=self.cdp_url,
			**kwargs,
		)
		# Share the browser state
		copy._playwright = self._playwright
		copy._browser = self._browser
		copy._browser_context = self._browser_context
		copy._subprocess = self._subprocess
		copy._owns_browser_resources = False  # Copy doesn't own resources
		return copy

	# Additional compatibility methods
	async def is_connected(self) -> bool:
		"""Check if connected to browser."""
		return self._browser is not None and self._browser.is_connected()

	async def save_storage_state(self, path: str | None = None) -> None:
		"""Save browser storage state."""
		if not self._browser_context:
			return

		save_path = path or self.browser_profile.storage_state
		if save_path:
			await self._browser_context.storage_state(path=str(save_path))

	async def get_tabs_info(self) -> list[dict[str, Any]]:
		"""Get information about all open tabs."""
		# Dispatch the request event
		self.event_bus.dispatch(TabsInfoRequestEvent())
		# Wait for the response event
		try:
			response = await self.event_bus.expect(TabsInfoResponseEvent, timeout=5.0)
			return response.tabs
		except TimeoutError:
			# No response received
			return []

	# DOM element methods
	async def get_browser_state_with_recovery(
		self, cache_clickable_elements_hashes: bool = True, include_screenshot: bool = True
	) -> Any:
		"""Get browser state with multiple fallback strategies for error recovery

		Parameters:
		-----------
		cache_clickable_elements_hashes: bool
			If True, cache the clickable elements hashes for the current state.
		include_screenshot: bool
			If True, include screenshot in the state summary. Set to False to improve performance
			when screenshots are not needed (e.g., in multi_act element validation).
		"""
		# Dispatch request event
		self.event_bus.dispatch(
			BrowserStateRequestEvent(
				include_dom=True,
				include_screenshot=include_screenshot,
				cache_clickable_elements_hashes=cache_clickable_elements_hashes,
			)
		)

		# Wait for response
		try:
			response = await self.event_bus.expect(BrowserStateResponseEvent, timeout=60.0)
			return response.state
		except TimeoutError:
			# Fall back to minimal state
			return await self._get_minimal_state_summary()

	async def _get_browser_state_with_recovery(
		self, cache_clickable_elements_hashes: bool = True, include_screenshot: bool = True
	) -> Any:
		"""Internal method to get browser state with recovery logic."""
		# Try 1: Full state summary
		try:
			await self._wait_for_page_and_frames_load()
			return await self._get_state_summary(cache_clickable_elements_hashes, include_screenshot=include_screenshot)
		except Exception as e:
			logger.warning(f'Full state retrieval failed: {type(e).__name__}: {e}')

		logger.warning('🔄 Falling back to minimal state summary')
		return await self._get_minimal_state_summary()

	async def _wait_for_page_and_frames_load(self, timeout_overwrite: float | None = None):
		"""
		Ensures page is fully loaded and stable before continuing.
		Waits for network idle, DOM stability, and minimum WAIT_TIME.
		"""
		# For now, just ensure we have a page
		page = await self.get_current_page()
		if not page:
			raise ValueError('No current page available')

		# Skip wait for new tab pages
		if page.url in ['about:blank', 'chrome://new-tab-page/', 'chrome://newtab/']:
			return

		# Basic wait for load state
		try:
			await page.wait_for_load_state('networkidle', timeout=timeout_overwrite or 30000)
		except Exception:
			# Continue even if timeout
			pass

	async def _get_state_summary(self, cache_clickable_elements_hashes: bool, include_screenshot: bool = True) -> Any:
		"""Get a summary of the current browser state"""
		from browser_use.browser.views import BrowserStateSummary, PageInfo
		from browser_use.dom.service import DomService

		# Auto-start if needed
		if not self.initialized:
			await self.start()

		page = await self.get_current_page()

		# Use DomService to get DOM content like the original implementation
		dom_service = DomService(page, logger=logger)
		try:
			content = await asyncio.wait_for(
				dom_service.get_clickable_elements(
					focus_element=-1,
					viewport_expansion=self.browser_profile.viewport_expansion,
					highlight_elements=self.browser_profile.highlight_elements,
				),
				timeout=45.0,
			)
		except TimeoutError:
			logger.warning(f'DOM processing timed out after 45 seconds for {page.url}')
			# Fall back to minimal DOM
			from browser_use.dom.views import DOMElementNode, DOMState

			minimal_element_tree = DOMElementNode(
				tag_name='body',
				xpath='/body',
				attributes={},
				children=[],
				is_visible=True,
				parent=None,
			)
			content = DOMState(element_tree=minimal_element_tree, selector_map={})

		# Get tabs info
		tabs_info = await self.get_tabs_info()

		# Get screenshot if requested
		screenshot_b64 = None
		if include_screenshot:
			try:
				screenshot_bytes = await page.screenshot()
				screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
			except Exception:
				pass

		# Get page dimensions
		try:
			page_dimensions = await page.evaluate("""
				() => ({
					viewport_width: window.innerWidth,
					viewport_height: window.innerHeight,
					page_width: document.documentElement.scrollWidth,
					page_height: document.documentElement.scrollHeight,
					scroll_x: window.scrollX,
					scroll_y: window.scrollY
				})
			""")

			page_info = PageInfo(
				viewport_width=page_dimensions['viewport_width'],
				viewport_height=page_dimensions['viewport_height'],
				page_width=page_dimensions['page_width'],
				page_height=page_dimensions['page_height'],
				scroll_x=page_dimensions['scroll_x'],
				scroll_y=page_dimensions['scroll_y'],
				pixels_above=page_dimensions['scroll_y'],
				pixels_below=max(
					0, page_dimensions['page_height'] - page_dimensions['scroll_y'] - page_dimensions['viewport_height']
				),
				pixels_left=page_dimensions['scroll_x'],
				pixels_right=max(
					0, page_dimensions['page_width'] - page_dimensions['scroll_x'] - page_dimensions['viewport_width']
				),
			)
		except Exception:
			# Fallback page info
			viewport = page.viewport_size or {'width': 1280, 'height': 720}
			page_info = PageInfo(
				viewport_width=viewport['width'],
				viewport_height=viewport['height'],
				page_width=viewport['width'],
				page_height=viewport['height'],
				scroll_x=0,
				scroll_y=0,
				pixels_above=0,
				pixels_below=0,
				pixels_left=0,
				pixels_right=0,
			)

		return BrowserStateSummary(
			element_tree=content.element_tree,
			selector_map=content.selector_map,
			url=page.url,
			title=await page.title(),
			tabs=tabs_info,
			screenshot=screenshot_b64,
			page_info=page_info,
			pixels_above=page_info.pixels_above,
			pixels_below=page_info.pixels_below,
		)

	async def _get_minimal_state_summary(self) -> Any:
		"""Get basic page info without DOM processing"""
		from browser_use.browser.views import BrowserStateSummary
		from browser_use.dom.views import DOMElementNode

		page = await self.get_current_page()

		# Get basic info
		url = getattr(page, 'url', 'unknown')

		try:
			title = await asyncio.wait_for(page.title(), timeout=2.0)
		except Exception:
			title = 'Page Load Error'

		try:
			tabs_info = await self.get_tabs_info()
		except Exception:
			tabs_info = []

		# Create minimal DOM element
		minimal_element_tree = DOMElementNode(
			tag_name='body',
			xpath='/body',
			attributes={},
			children=[],
			is_visible=True,
			parent=None,
		)

		return BrowserStateSummary(
			element_tree=minimal_element_tree,
			selector_map={},
			url=url,
			title=title,
			tabs=tabs_info,
			pixels_above=0,
			pixels_below=0,
		)

	async def get_selector_map(self) -> dict:
		"""Get selector map."""
		# TODO: Implement from old BrowserSession
		return {}

	async def get_dom_element_by_index(self, index: int) -> Any:
		"""Get DOM element by index."""
		# TODO: Implement from old BrowserSession
		raise NotImplementedError('get_dom_element_by_index needs to be implemented')

	async def find_file_upload_element_by_index(self, index: int) -> Any:
		"""Find file upload element by index."""
		# TODO: Implement from old BrowserSession
		raise NotImplementedError('find_file_upload_element_by_index needs to be implemented')

	async def get_locate_element(self, dom_element: Any) -> Any:
		"""Get locate element."""
		# TODO: Implement from old BrowserSession
		raise NotImplementedError('get_locate_element needs to be implemented')

	def is_file_input(self, element: Any) -> bool:
		"""Check if element is a file input."""
		# TODO: Implement from old BrowserSession
		return False

	async def _click_element_node(self, element: Any) -> str | None:
		"""Click an element node."""
		# TODO: Implement from old BrowserSession
		raise NotImplementedError('_click_element_node needs to be implemented')

	async def _input_text_element_node(self, element: Any, text: str) -> None:
		"""Input text into an element node."""
		# TODO: Implement from old BrowserSession
		raise NotImplementedError('_input_text_element_node needs to be implemented')

	async def _scroll_container(self, dy: int) -> None:
		"""Scroll container."""
		# TODO: Implement from old BrowserSession
		raise NotImplementedError('_scroll_container needs to be implemented')


# Import uuid7str for ID generation
try:
	from uuid_extensions import uuid7str
except ImportError:
	import uuid

	def uuid7str() -> str:
		return str(uuid.uuid4())
