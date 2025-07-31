"""Event-driven browser session with backwards compatibility."""

import asyncio
import base64
import json
import os
import warnings
from typing import TYPE_CHECKING, Any, Self

import anyio
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
	FileDownloadedEvent,
	LoadStorageStateEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	SaveStorageStateEvent,
	ScreenshotRequestEvent,
	ScreenshotResponseEvent,
	ScrollEvent,
	StartBrowserEvent,
	StopBrowserEvent,
	StorageStateLoadedEvent,
	StorageStateSavedEvent,
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
	
	# PDF handling
	_auto_download_pdfs: bool = PrivateAttr(default=True)
	_downloaded_files: list[str] = PrivateAttr(default_factory=list)
	
	# Watchdogs
	_crash_watchdog: Any = PrivateAttr(default=None)
	_downloads_watchdog: Any = PrivateAttr(default=None)

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
		
		# Storage state
		self.event_bus.on(SaveStorageStateEvent, self._handle_save_storage_state)
		self.event_bus.on(LoadStorageStateEvent, self._handle_load_storage_state)

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
				# Get context kwargs
				new_context_args = self.browser_profile.kwargs_for_new_context()
				context_kwargs = new_context_args.model_dump(exclude_none=True)

				# Log storage state info
				logger.info(f'BrowserProfile storage_state: {self.browser_profile.storage_state}')
				logger.info(f'NewContextArgs storage_state: {new_context_args.storage_state}')
				logger.info(f'Context kwargs keys: {list(context_kwargs.keys())}')
				logger.info(f'Context kwargs storage_state: {context_kwargs.get("storage_state")}')

				self._browser_context = await self._browser.new_context(**context_kwargs)

			# Set initial page if exists
			pages = self._browser_context.pages
			if pages:
				self._current_agent_page = pages[0]
				self._current_human_page = pages[0]
				
			# Initialize crash watchdog if not already initialized
			if not hasattr(self, '_crash_watchdog'):
				from browser_use.browser.crash_watchdog import CrashWatchdog
				self._crash_watchdog = CrashWatchdog(event_bus=self.event_bus)
				# Set browser context for crash watchdog
				self._crash_watchdog.set_browser_context(self._browser, self._browser_context, self._subprocess.pid if self._subprocess else None)
				# Add initial pages to crash watchdog
				for page in pages:
					self._crash_watchdog.add_page(page)
					
			# Initialize downloads watchdog if not already initialized
			if not hasattr(self, '_downloads_watchdog'):
				from browser_use.browser.downloads_watchdog import DownloadsWatchdog
				self._downloads_watchdog = DownloadsWatchdog(event_bus=self.event_bus)
				# Add initial pages to downloads watchdog
				for page in pages:
					self._downloads_watchdog.add_page(page)

			# Notify success
			self.event_bus.dispatch(
				BrowserStartedEvent(
					cdp_url=self.cdp_url,
					browser_pid=self._subprocess.pid if self._subprocess else None,
				)
			)
			
			# Automatically load storage state after browser start
			self.event_bus.dispatch(LoadStorageStateEvent())

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
			
			# Automatically save storage state before stopping
			self.event_bus.dispatch(SaveStorageStateEvent())
			# Give it a moment to save
			await asyncio.sleep(0.1)

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

				# Clean up temp directory if one was created
				if self.browser_profile.user_data_dir and 'browseruse-tmp-' in str(self.browser_profile.user_data_dir):
					LocalBrowserHelpers.cleanup_temp_dir(self.browser_profile.user_data_dir)

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
			
			tab_index = self.tabs.index(self._current_agent_page)
			
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=tab_index,
					url=event.url,
					status=response.status if response else None,
				)
			)
			
			# Check for PDF after navigation and auto-download if enabled
			if self._auto_download_pdfs and await self._is_pdf_viewer(self._current_agent_page):
				pdf_path = await self._auto_download_pdf_if_needed(self._current_agent_page)
				if pdf_path:
					logger.info(f'📄 PDF auto-downloaded: {pdf_path}')
						
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
		# TODO: This is a simplified implementation until DOM tracking is ported
		if not self._current_agent_page:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NoActivePage',
					message='No active page for click',
				)
			)
			return
			
		try:
			# For now, we'll implement basic download detection
			# Full implementation will need DOM element tracking from old session
			page = self._current_agent_page
			
			# Set up download handling if downloads are enabled
			if self.browser_profile.downloads_path:
				# Register download listener
				download_promise = None
				
				async def handle_download(download):
					nonlocal download_promise
					download_promise = download
				
				# Temporarily attach download listener
				page.on('download', handle_download)
				
				# TODO: Perform actual click when DOM tracking is implemented
				# For now this is a placeholder
				self.event_bus.dispatch(
					BrowserErrorEvent(
						error_type='NotImplemented',
						message='Click handling needs full DOM element tracking implementation',
					)
				)
				
				# Clean up listener
				page.remove_listener('download', handle_download)
				
				# If a download was triggered, handle it
				if download_promise:
					await self._handle_download(download_promise)
			else:
				# TODO: Perform click without download handling
				self.event_bus.dispatch(
					BrowserErrorEvent(
						error_type='NotImplemented',
						message='Click handling needs full DOM element tracking implementation',
					)
				)
				
		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='ClickFailed',
					message=str(e),
					details={'index': event.index},
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
		
		# Add page to crash watchdog
		if hasattr(self, '_crash_watchdog') and self._crash_watchdog:
			self._crash_watchdog.add_page(page)
			
		# Add page to downloads watchdog
		if hasattr(self, '_downloads_watchdog') and self._downloads_watchdog:
			self._downloads_watchdog.add_page(page)
		
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

	async def _handle_save_storage_state(self, event: SaveStorageStateEvent) -> None:
		"""Handle save storage state request."""
		if not self._browser_context:
			logger.warning('_handle_save_storage_state: No browser context available')
			return
		
		save_path = event.path or self.browser_profile.storage_state
		if save_path:
			storage = await self._browser_context.storage_state(path=str(save_path))
			self.event_bus.dispatch(
				StorageStateSavedEvent(
					path=str(save_path),
					cookies_count=len(storage.get('cookies', [])),
					origins_count=len(storage.get('origins', []))
				)
			)


	def _generate_recent_events_summary(self, max_events: int = 10) -> str:
		"""Generate a JSON summary of recent browser events."""
		import json
		
		# Get recent events from the event bus history (it's a dict of UUID -> Event)
		all_events = list(self.event_bus.event_history.values())
		recent_events = all_events[-max_events:] if all_events else []
		
		if not recent_events:
			return "[]"
		
		# Convert events to JSON
		events_data = []
		for event in recent_events:
			# Exclude fields that might cause circular references
			# BrowserStateResponseEvent has 'state' which can be circular
			event_dict = event.model_dump(mode='json', exclude={'state'})
			events_data.append(event_dict)
		
		return json.dumps(events_data, indent=2)

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
		return self._downloaded_files.copy()

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
		# Use event-based approach
		self.event_bus.dispatch(SaveStorageStateEvent(path=path))

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

		# Check for PDF and auto-download if needed
		is_pdf_viewer = await self._is_pdf_viewer(page)
		if is_pdf_viewer and self._auto_download_pdfs:
			pdf_path = await self._auto_download_pdf_if_needed(page)
			if pdf_path:
				logger.info(f'📄 PDF auto-downloaded: {pdf_path}')

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
			is_pdf_viewer=is_pdf_viewer,
			recent_events=self._generate_recent_events_summary(),
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

		# Check if current page is a PDF viewer
		is_pdf_viewer = False
		try:
			is_pdf_viewer = await self._is_pdf_viewer(page)
		except Exception:
			pass

		return BrowserStateSummary(
			element_tree=minimal_element_tree,
			selector_map={},
			url=url,
			title=title,
			tabs=tabs_info,
			pixels_above=0,
			pixels_below=0,
			is_pdf_viewer=is_pdf_viewer,
			recent_events=self._generate_recent_events_summary(),
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

	def _is_url_allowed(self, url: str) -> bool:
		"""Check if a URL is allowed based on the allowed_domains configuration.

		Args:
			url: The URL to check

		Returns:
			True if the URL is allowed, False otherwise
		"""
		# If no allowed_domains specified, allow all URLs
		if not self.browser_profile.allowed_domains:
			return True

		# Always allow internal browser pages
		if url in ['about:blank', 'chrome://new-tab-page/', 'chrome://new-tab-page', 'chrome://newtab/']:
			return True

		# Parse the URL to extract components
		from urllib.parse import urlparse

		try:
			parsed = urlparse(url)
		except Exception:
			# Invalid URL
			return False

		# Get the actual host (domain)
		host = parsed.hostname
		if not host:
			return False

		# Full URL for matching (scheme + host)
		full_url_pattern = f'{parsed.scheme}://{host}'

		# Check each allowed domain pattern
		for pattern in self.browser_profile.allowed_domains:
			# Handle glob patterns
			if '*' in pattern:
				import fnmatch

				# Check if pattern matches the host
				if pattern.startswith('*.'):
					# Pattern like *.example.com should match subdomains and main domain
					# But only for http/https URLs unless scheme is specified
					domain_part = pattern[2:]  # Remove *.
					if host == domain_part or host.endswith('.' + domain_part):
						# Only match http/https URLs for domain-only patterns
						if parsed.scheme in ['http', 'https']:
							return True
				elif pattern.endswith('/*'):
					# Pattern like brave://* should match any brave:// URL
					prefix = pattern[:-1]  # Remove the * at the end
					if url.startswith(prefix):
						return True
				else:
					# Use fnmatch for other glob patterns
					if fnmatch.fnmatch(host, pattern):
						return True
			else:
				# Exact match
				if pattern.startswith(('http://', 'https://', 'chrome://', 'brave://', 'file://')):
					# Full URL pattern
					if url.startswith(pattern):
						return True
				else:
					# Domain-only pattern
					if host == pattern:
						return True

		return False
	
	# ========== Storage State Handlers ==========
	
	async def _handle_save_storage_state(self, event: SaveStorageStateEvent) -> None:
		"""Handle storage state save request."""
		if not self._browser_context:
			logger.warning('save_storage_state: No browser context available')
			return
		
		save_path = event.path or self.browser_profile.storage_state
		if save_path:
			logger.info(f'Saving storage state to: {save_path}')
			storage = await self._browser_context.storage_state(path=str(save_path))
			
			# Dispatch success event
			self.event_bus.dispatch(
				StorageStateSavedEvent(
					path=str(save_path),
					cookies_count=len(storage.get("cookies", [])),
					origins_count=len(storage.get("origins", []))
				)
			)
	
	async def _handle_load_storage_state(self, event: LoadStorageStateEvent) -> None:
		"""Handle storage state load request."""
		# Storage state is loaded during browser context creation
		# This handler is mainly for notification purposes
		load_path = event.path or self.browser_profile.storage_state
		if load_path and os.path.exists(str(load_path)):
			# Read the file to get counts
			try:
				with open(str(load_path), 'r') as f:
					storage = json.load(f)
				
				self.event_bus.dispatch(
					StorageStateLoadedEvent(
						path=str(load_path),
						cookies_count=len(storage.get("cookies", [])),
						origins_count=len(storage.get("origins", []))
					)
				)
				logger.info(f'Loaded storage state from: {load_path}')
			except Exception as e:
				logger.warning(f'Failed to read storage state for notification: {e}')
	
	# ========== PDF Methods ==========
	
	async def _is_pdf_viewer(self, page: Page) -> bool:
		"""
		Check if the current page is displaying a PDF in Chrome's PDF viewer.
		Returns True if PDF is detected, False otherwise.
		"""
		try:
			is_pdf_viewer = await page.evaluate("""
				() => {
					// Check for Chrome's built-in PDF viewer (updated selector)
					const pdfEmbed = document.querySelector('embed[type="application/x-google-chrome-pdf"]') ||
									 document.querySelector('embed[type="application/pdf"]');
					const isPdfViewer = !!pdfEmbed;
					
					// Also check if the URL ends with .pdf or has PDF content-type
					const url = window.location.href;
					const isPdfUrl = url.toLowerCase().includes('.pdf') || 
									document.contentType === 'application/pdf';
					
					return isPdfViewer || isPdfUrl;
				}
			""")
			return is_pdf_viewer
		except Exception as e:
			logger.debug(f'Error checking PDF viewer: {type(e).__name__}: {e}')
			return False

	async def _auto_download_pdf_if_needed(self, page: Page) -> str | None:
		"""
		Check if the current page is a PDF viewer and automatically download the PDF if so.
		Returns the download path if a PDF was downloaded, None otherwise.
		"""
		if not self.browser_profile.downloads_path or not self._auto_download_pdfs:
			return None

		try:
			# Check if we're in a PDF viewer
			is_pdf_viewer = await self._is_pdf_viewer(page)
			logger.debug(f'is_pdf_viewer: {is_pdf_viewer}')

			if not is_pdf_viewer:
				return None

			# Get the PDF URL
			pdf_url = page.url

			# Check if we've already downloaded this PDF
			pdf_filename = os.path.basename(pdf_url.split('?')[0])  # Remove query params
			if not pdf_filename or not pdf_filename.endswith('.pdf'):
				# Generate filename from URL
				from urllib.parse import urlparse

				parsed = urlparse(pdf_url)
				pdf_filename = os.path.basename(parsed.path) or 'document.pdf'
				if not pdf_filename.endswith('.pdf'):
					pdf_filename += '.pdf'

			# Check if already downloaded
			expected_path = os.path.join(self.browser_profile.downloads_path, pdf_filename)
			if any(os.path.basename(downloaded) == pdf_filename for downloaded in self._downloaded_files):
				logger.debug(f'📄 PDF already downloaded: {pdf_filename}')
				return None

			logger.info(f'📄 Auto-downloading PDF from: {pdf_url}')

			# Download the actual PDF file using JavaScript fetch
			# Note: This should hit the browser cache since Chrome already downloaded the PDF to display it
			try:
				logger.debug(f'Downloading PDF from URL: {pdf_url}')

				# Properly escape the URL to prevent JavaScript injection
				escaped_pdf_url = json.dumps(pdf_url)

				download_result = await page.evaluate(f"""
					async () => {{
						try {{
							// Use fetch with cache: 'force-cache' to prioritize cached version
							const response = await fetch({escaped_pdf_url}, {{
								cache: 'force-cache'
							}});
							if (!response.ok) {{
								throw new Error(`HTTP error! status: ${{response.status}}`);
							}}
							const blob = await response.blob();
							const arrayBuffer = await blob.arrayBuffer();
							const uint8Array = new Uint8Array(arrayBuffer);
							
							// Log whether this was served from cache
							const fromCache = response.headers.has('age') || 
											 !response.headers.has('date') ||
											 performance.getEntriesByName({escaped_pdf_url}).some(entry => 
												 entry.transferSize === 0 || entry.transferSize < entry.encodedBodySize
											 );
											 
							return {{ 
								data: Array.from(uint8Array),
								fromCache: fromCache,
								responseSize: uint8Array.length,
								transferSize: response.headers.get('content-length') || 'unknown'
							}};
						}} catch (error) {{
							throw new Error(`Fetch failed: ${{error.message}}`);
						}}
					}}
				""")

				if download_result and download_result.get('data') and len(download_result['data']) > 0:
					# Ensure unique filename
					unique_filename = await self._get_unique_filename(self.browser_profile.downloads_path, pdf_filename)
					download_path = os.path.join(self.browser_profile.downloads_path, unique_filename)

					# Save the PDF asynchronously
					async with await anyio.open_file(download_path, 'wb') as f:
						await f.write(bytes(download_result['data']))

					# Track the downloaded file
					self._track_download(download_path)

					# Log cache information
					cache_status = 'from cache' if download_result.get('fromCache') else 'from network'
					response_size = download_result.get('responseSize', 0)
					logger.info(f'📄 Auto-downloaded PDF ({cache_status}, {response_size:,} bytes): {download_path}')
					
					# Emit file downloaded event
					await self._emit_file_downloaded_event(
						url=pdf_url,
						path=download_path,
						file_size=response_size,
						file_type='pdf',
						mime_type='application/pdf',
						from_cache=download_result.get('fromCache', False),
						auto_download=True,
					)

					return download_path
				else:
					logger.warning(f'⚠️ No data received when downloading PDF from {pdf_url}')
					return None

			except Exception as e:
				logger.warning(f'⚠️ Failed to auto-download PDF from {pdf_url}: {type(e).__name__}: {e}')
				return None

		except Exception as e:
			logger.warning(f'⚠️ Error in PDF auto-download check: {type(e).__name__}: {e}')
			return None
	
	@staticmethod
	async def _get_unique_filename(directory: str, filename: str) -> str:
		"""Generate a unique filename for downloads by appending (1), (2), etc., if a file already exists."""
		base, ext = os.path.splitext(filename)
		counter = 1
		new_filename = filename
		while os.path.exists(os.path.join(directory, new_filename)):
			new_filename = f'{base} ({counter}){ext}'
			counter += 1
		return new_filename
	
	# ========== Download Tracking Methods ==========
	
	async def _handle_download(self, download) -> str:
		"""Handle a Playwright download object and emit appropriate events.
		
		Args:
			download: Playwright Download object
			
		Returns:
			Path where the file was saved
		"""
		try:
			# Get download info
			suggested_filename = download.suggested_filename
			download_url = download.url
			
			# Ensure unique filename
			unique_filename = await self._get_unique_filename(
				self.browser_profile.downloads_path, 
				suggested_filename
			)
			download_path = os.path.join(
				self.browser_profile.downloads_path, 
				unique_filename
			)
			
			# Save the download
			await download.save_as(download_path)
			logger.info(f'⬇️ Downloaded file to: {download_path}')
			
			# Track the download
			self._track_download(download_path)
			
			# Emit download event
			await self._emit_file_downloaded_event(
				url=download_url,
				path=download_path,
				auto_download=False,  # This is a user-initiated download
			)
			
			return download_path
			
		except Exception as e:
			logger.error(f'Failed to handle download: {e}')
			raise
	
	def _track_download(self, download_path: str) -> None:
		"""Track a downloaded file internally."""
		self._downloaded_files.append(download_path)
		logger.debug(f'📁 Added download to session tracking: {download_path} (total: {len(self._downloaded_files)} files)')
	
	async def _emit_file_downloaded_event(
		self, 
		url: str, 
		path: str, 
		file_size: int | None = None,
		file_type: str | None = None,
		mime_type: str | None = None,
		from_cache: bool = False,
		auto_download: bool = False
	) -> None:
		"""Emit a FileDownloadedEvent with file information."""
		import os
		from pathlib import Path
		
		file_path = Path(path)
		file_name = file_path.name
		
		# Try to determine file type from extension if not provided
		if not file_type and file_path.suffix:
			file_type = file_path.suffix.lstrip('.')
		
		# Get file size if not provided
		if file_size is None and file_path.exists():
			file_size = file_path.stat().st_size
		
		self.event_bus.dispatch(
			FileDownloadedEvent(
				url=url,
				path=str(path),
				file_name=file_name,
				file_size=file_size or 0,
				file_type=file_type,
				mime_type=mime_type,
				from_cache=from_cache,
				auto_download=auto_download,
			)
		)
	
	# ========== PDF API Methods ==========
	
	def set_auto_download_pdfs(self, enabled: bool) -> None:
		"""
		Enable or disable automatic PDF downloading when PDFs are encountered.
		
		Args:
		    enabled: Whether to automatically download PDFs
		"""
		self._auto_download_pdfs = enabled
		logger.info(f'📄 PDF auto-download {"enabled" if enabled else "disabled"}')

	@property
	def auto_download_pdfs(self) -> bool:
		"""Get current PDF auto-download setting."""
		return self._auto_download_pdfs


# Import uuid7str for ID generation
try:
	from uuid_extensions import uuid7str
except ImportError:
	import uuid

	def uuid7str() -> str:
		return str(uuid.uuid4())
