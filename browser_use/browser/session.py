"""Event-driven browser session with backwards compatibility."""

import asyncio
import base64
import json
import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self
from weakref import WeakKeyDictionary, WeakSet

from bubus import EventBus
from playwright.async_api import Browser, BrowserContext, FloatRect, Page, Playwright, Request, Response, async_playwright
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
	FileDownloadedEvent,
	InputTextEvent,
	LoadStorageStateEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	NavigationStartedEvent,
	SaveStorageStateEvent,
	ScreenshotRequestEvent,
	ScreenshotResponseEvent,
	ScrollEvent,
	StartBrowserEvent,
	StopBrowserEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
	TabsInfoRequestEvent,
	TabsInfoResponseEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.views import TabInfo
from browser_use.utils import logger

if TYPE_CHECKING:
	pass

# Default browser profile for convenience
DEFAULT_BROWSER_PROFILE = BrowserProfile()

# Common new tab page URLs
NEW_TAB_URLS = ['about:blank', 'chrome://new-tab-page/', 'chrome://newtab/']


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

	# Local browser state (only used when cdp_url is None)
	_subprocess: Any = PrivateAttr(default=None)  # psutil.Process
	_owns_browser_resources: bool = PrivateAttr(default=True)

	# PDF handling
	_auto_download_pdfs: bool = PrivateAttr(default=True)

	# Watchdogs
	_crash_watchdog: Any = PrivateAttr(default=None)
	_downloads_watchdog: Any = PrivateAttr(default=None)
	_aboutblank_watchdog: Any = PrivateAttr(default=None)
	_human_focus_watchdog: Any = PrivateAttr(default=None)
	_agent_focus_watchdog: Any = PrivateAttr(default=None)
	_storage_state_watchdog: Any = PrivateAttr(default=None)

	# Navigation tracking state (merged from NavigationWatchdog)
	_tracked_pages: WeakSet[Page] = PrivateAttr(default_factory=WeakSet)
	_page_requests: WeakKeyDictionary[Page, set[Request]] = PrivateAttr(default_factory=WeakKeyDictionary)
	_page_last_activity: WeakKeyDictionary[Page, float] = PrivateAttr(default_factory=WeakKeyDictionary)
	_monitoring_tasks: WeakKeyDictionary[Page, asyncio.Task] = PrivateAttr(default_factory=WeakKeyDictionary)

	def __init__(
		self,
		browser_profile: BrowserProfile,
		cdp_url: str | None = None,
		browser_pid: int | None = None,
		**kwargs: Any,
	):
		"""Initialize a browser session.

		Args:
			browser_profile: Browser configuration profile
			cdp_url: CDP URL for connecting to existing browser
			browser_pid: Process ID of existing browser (DEPRECATED)
			**kwargs: Additional arguments
		"""

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

		# Storage state is handled by StorageStateWatchdog

	# ========== Event Handlers ==========

	async def _handle_start(self, event: StartBrowserEvent) -> None:
		"""Handle browser start request."""
		if self._browser and self._browser.is_connected():
			# Already started
			if not self.cdp_url:
				raise ValueError('No CDP URL available for browser connection')
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

			# Ensure we have a CDP URL at this point
			if not self.cdp_url:
				raise ValueError('No CDP URL available for browser connection')

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

				# Map downloads_path to downloads_path for new_context and ensure accept_downloads is True
				if self.browser_profile.downloads_path:
					# Ensure downloads directory exists
					downloads_dir = Path(self.browser_profile.downloads_path)
					downloads_dir.mkdir(parents=True, exist_ok=True)
					context_kwargs['accept_downloads'] = True
					context_kwargs['downloads_path'] = str(downloads_dir.absolute())
					logger.info(f'Setting downloads_path to: {context_kwargs["downloads_path"]}')
					logger.info(f'Accept downloads: {context_kwargs["accept_downloads"]}')

				# Log storage state info
				logger.info(f'BrowserProfile storage_state: {self.browser_profile.storage_state}')
				logger.info(f'NewContextArgs storage_state: {new_context_args.storage_state}')
				logger.info(f'Context kwargs keys: {list(context_kwargs.keys())}')
				logger.info(f'Context kwargs storage_state: {context_kwargs.get("storage_state")}')
				logger.info(f'Context kwargs downloads_path: {context_kwargs.get("downloads_path")}')
				logger.info(f'Context kwargs accept_downloads: {context_kwargs.get("accept_downloads")}')

				self._browser_context = await self._browser.new_context(**context_kwargs)

			# Set initial page if exists
			pages = self._browser_context.pages
			# Agent focus will be initialized by the watchdog

			# Initialize crash watchdog if not already initialized
			if not hasattr(self, '_crash_watchdog'):
				from browser_use.browser.crash_watchdog import CrashWatchdog

				self._crash_watchdog = CrashWatchdog(event_bus=self.event_bus)
				# Set browser context for crash watchdog
				self._crash_watchdog.set_browser_context(
					self._browser, self._browser_context, self._subprocess.pid if self._subprocess else None
				)

			# Initialize downloads watchdog if not already initialized
			if not hasattr(self, '_downloads_watchdog'):
				from browser_use.browser.downloads_watchdog import DownloadsWatchdog

				self._downloads_watchdog = DownloadsWatchdog(event_bus=self.event_bus, browser_session=self)

			# Add initial pages to all watchdogs
			for page in pages:
				self._add_page_to_watchdogs(page)

			# Initialize aboutblank watchdog if not already initialized
			if not hasattr(self, '_aboutblank_watchdog'):
				from browser_use.browser.aboutblank_watchdog import AboutBlankWatchdog

				self._aboutblank_watchdog = AboutBlankWatchdog(event_bus=self.event_bus, browser_session=self)

			# Initialize human focus watchdog if not already initialized
			if not hasattr(self, '_human_focus_watchdog'):
				from browser_use.browser.human_focus_watchdog import HumanFocusWatchdog

				self._human_focus_watchdog = HumanFocusWatchdog(event_bus=self.event_bus, browser_session=self)

			# Initialize agent focus watchdog if not already initialized
			if not hasattr(self, '_agent_focus_watchdog'):
				from browser_use.browser.agent_focus_watchdog import AgentFocusWatchdog

				self._agent_focus_watchdog = AgentFocusWatchdog(event_bus=self.event_bus, browser_session=self)

			# Initialize storage state watchdog if not already initialized
			if not hasattr(self, '_storage_state_watchdog'):
				from browser_use.browser.storage_state_watchdog import StorageStateWatchdog

				self._storage_state_watchdog = StorageStateWatchdog(event_bus=self.event_bus, browser_session=self)

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

			# Cancel all navigation monitoring tasks
			for task in list(self._monitoring_tasks.values()):
				if not task.done():
					task.cancel()

			# Clear navigation tracking state
			self._tracked_pages.clear()
			self._page_requests.clear()
			self._page_last_activity.clear()
			self._monitoring_tasks.clear()

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
		"""Handle navigation request with network monitoring (merged from NavigationWatchdog)."""
		try:
			# Get the page to navigate
			page = await self._get_page_for_navigation(event)
			if not page:
				self.event_bus.dispatch(
					BrowserErrorEvent(
						error_type='NoActivePage',
						message='No active page to navigate',
					)
				)
				return

			# Dispatch navigation started event
			tab_index = await self._get_tab_index(page)
			self.event_bus.dispatch(
				NavigationStartedEvent(
					tab_index=tab_index,
					url=event.url,
				)
			)

			# Perform navigation
			response = await page.goto(
				event.url,
				wait_until=event.wait_until,
			)

			# Dispatch completion event
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=tab_index,
					url=event.url,
					status=response.status if response else None,
				)
			)

			# Start monitoring network activity for the page
			await self._monitor_page_network(page)

		except Exception as e:
			# Handle navigation errors
			error_message = str(e)
			loading_status = None

			# Check for timeout errors
			if 'timeout' in error_message.lower():
				loading_status = f'Timed out waiting for network idle after {event.wait_until}'
				if 'exceeded while waiting for event "load"' in error_message:
					loading_status = 'Network timeout - page load incomplete'

			# Get tab index for error reporting
			try:
				page = await self._get_page_for_navigation(event)
				tab_index = await self._get_tab_index(page) if page else 0
			except Exception:
				tab_index = 0

			# Dispatch NavigationCompleteEvent with error details
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=tab_index,
					url=event.url,
					status=None,
					error_message=error_message,
					loading_status=loading_status,
				)
			)

			# Also dispatch error event for backwards compatibility
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NavigationFailed',
					message=str(e),
					details={'url': event.url},
				)
			)

	async def _handle_click(self, event: ClickElementEvent) -> None:
		"""Handle click request."""
		try:
			page = await self.get_current_page()
		except ValueError:
			self._dispatch_no_page_error('NoActivePage', 'No active page for click')
			return

		try:
			# Get the DOM element by index
			element_node = await self.get_dom_element_by_index(event.index)
			if element_node is None:
				raise Exception(f'Element index {event.index} does not exist - retry or use alternative actions')

			# Track initial number of tabs to detect new tab opening
			initial_pages = len(self.tabs)

			# Check if element is a file input (should not be clicked)
			if self.is_file_input(element_node):
				msg = f'Index {event.index} - has an element which opens file upload dialog. To upload files please use a specific function to upload files'
				logger.info(msg)
				self.event_bus.dispatch(
					BrowserErrorEvent(
						error_type='FileInputElement',
						message=msg,
						details={'index': event.index},
					)
				)
				return

			# Perform the actual click
			download_path = await self._click_element_node(element_node)

			# Build success message
			if download_path:
				msg = f'Downloaded file to {download_path}'
				logger.info(f'💾 {msg}')
			else:
				msg = f'Clicked button with index {event.index}: {element_node.get_all_text_till_next_clickable_element(max_depth=2)}'
				logger.info(f'🖱️ {msg}')

			logger.debug(f'Element xpath: {element_node.xpath}')

			# Check if a new tab was opened
			if len(self.tabs) > initial_pages:
				new_tab_msg = 'New tab opened - switching to it'
				msg += f' - {new_tab_msg}'
				logger.info(f'🔗 {new_tab_msg}')
				await self.switch_to_tab(-1)

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
		try:
			page = await self.get_current_page()
		except ValueError:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NoActivePage',
					message='No active page for text input',
				)
			)
			return

		try:
			# Get the DOM element by index
			element_node = await self.get_dom_element_by_index(event.index)
			if element_node is None:
				raise Exception(f'Element index {event.index} does not exist - retry or use alternative actions')

			# Perform the actual text input
			await self._input_text_element_node(element_node, event.text)

			# Log success
			logger.info(f'⌨️ Typed "{event.text}" into element with index {event.index}')
			logger.debug(f'Element xpath: {element_node.xpath}')

		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='InputTextFailed',
					message=str(e),
					details={'index': event.index, 'text': event.text},
				)
			)

	async def _handle_scroll(self, event: ScrollEvent) -> None:
		"""Handle scroll request."""
		try:
			page = await self.get_current_page()
		except ValueError:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NoActivePage',
					message='No active page for scrolling',
				)
			)
			return

		try:
			# Convert direction and amount to pixels
			# Positive pixels = scroll down, negative = scroll up
			pixels = event.amount if event.direction == 'down' else -event.amount

			# Perform the scroll
			await self._scroll_container(pixels)

			# Log success
			logger.info(f'📜 Scrolled {event.direction} by {event.amount} pixels')

		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='ScrollFailed',
					message=str(e),
					details={'direction': event.direction, 'amount': event.amount},
				)
			)

	async def _handle_switch_tab(self, event: SwitchTabEvent) -> None:
		"""Handle tab switch request."""
		# Agent focus watchdog will handle the actual switch via event listener
		pass

	async def _handle_create_tab(self, event: CreateTabEvent) -> None:
		"""Handle new tab creation."""
		if not self._browser_context:
			return

		page = await self._browser_context.new_page()

		# Note: Download behavior is set at browser level during startup

		# Add page to all watchdogs
		self._add_page_to_watchdogs(page)

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
			# Dispatch tab closed event for watchdogs
			self.event_bus.dispatch(TabClosedEvent(tab_index=event.tab_index))

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
		try:
			page = await self.get_current_page()
		except ValueError:
			return

		# Convert clip dict to FloatRect if provided
		clip_rect: FloatRect | None = None
		if event.clip:
			clip_rect = FloatRect(
				x=event.clip['x'],
				y=event.clip['y'],
				width=event.clip['width'],
				height=event.clip['height'],
			)

		screenshot_bytes = await page.screenshot(
			full_page=event.full_page,
			clip=clip_rect,
		)
		screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
		self.event_bus.dispatch(ScreenshotResponseEvent(screenshot=screenshot_b64))

	async def _handle_tabs_info_request(self, event: TabsInfoRequestEvent) -> None:
		"""Handle tabs info request."""
		from browser_use.browser.views import TabInfo

		# Auto-start if not initialized
		if not self.initialized:
			start_event = self.event_bus.dispatch(StartBrowserEvent())
			await start_event

		tabs = []
		for i, page in enumerate(self.tabs):
			if not page.is_closed():
				tab_info = TabInfo(
					page_id=i,
					url=page.url,
					title=await page.title(),
					parent_page_id=None,
					id=f'tab_{i}',
					index=i,
				)
				tabs.append(tab_info.model_dump())
		# Dispatch the response event
		self.event_bus.dispatch(TabsInfoResponseEvent(tabs=tabs))

	def _generate_recent_events_summary(self, max_events: int = 10) -> str:
		"""Generate a JSON summary of recent browser events."""

		# Get recent events from the event bus history (it's a dict of UUID -> Event)
		all_events = list(self.event_bus.event_history.values())
		recent_events = all_events[-max_events:] if all_events else []

		if not recent_events:
			return '[]'

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
		if self._agent_focus_watchdog:
			return self._agent_focus_watchdog.current_agent_page
		return None

	@property
	def human_current_page(self) -> Page | None:
		"""Get the human's current page."""
		if self._human_focus_watchdog:
			return self._human_focus_watchdog.current_human_page
		return None

	@property
	def downloaded_files(self) -> list[str]:
		"""Get list of downloaded files from the downloads directory, sorted by date (newest first)."""
		if not self.browser_profile.downloads_path:
			return []

		downloads_dir = self.browser_profile.downloads_path
		if not os.path.exists(downloads_dir):
			return []

		# List all files in the downloads directory with their modification times
		try:
			files_with_time = []
			for filename in os.listdir(downloads_dir):
				filepath = os.path.join(downloads_dir, filename)
				if os.path.isfile(filepath):
					# Get modification time
					mtime = os.path.getmtime(filepath)
					files_with_time.append((filepath, mtime))

			# Sort by modification time (newest first)
			files_with_time.sort(key=lambda x: x[1], reverse=True)

			# Return just the file paths
			return [filepath for filepath, _ in files_with_time]
		except Exception as e:
			logger.warning(f'Failed to list downloaded files: {e}')
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
		if self._agent_focus_watchdog:
			return await self._agent_focus_watchdog.get_or_create_page()
		# Fallback if watchdog not initialized
		if self.tabs:
			return self.tabs[0]
		raise ValueError('No active page available')

	async def new_page(self, url: str | None = None) -> Page:
		"""Create a new page."""
		event = self.event_bus.dispatch(CreateTabEvent(url=url))
		await event
		return self.tabs[-1]  # Return the newly created page

	async def create_new_tab(self, url: str | None = None) -> Page:
		"""Create a new tab."""
		return await self.new_page(url)

	async def new_tab(self, url: str | None = None) -> Page:
		"""Alias for create_new_tab for backward compatibility."""
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
		page = await self.get_current_page()
		if page:
			await page.go_back()

	async def go_forward(self) -> None:
		"""Go forward in the browser history."""
		page = await self.get_current_page()
		if page:
			await page.go_forward()

	async def refresh(self) -> None:
		"""Refresh the current page."""
		page = await self.get_current_page()
		if page:
			await page.reload()

	async def take_screenshot(self, full_page: bool = False, clip: dict | None = None) -> bytes:
		"""Take a screenshot."""
		# Dispatch the request event
		self.event_bus.dispatch(ScreenshotRequestEvent(full_page=full_page, clip=clip))
		# Wait for the response event
		try:
			event_result = await self.event_bus.expect(ScreenshotResponseEvent, timeout=10.0)
			response: ScreenshotResponseEvent = event_result  # type: ignore
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

	async def scroll(self, direction: Literal['up', 'down', 'left', 'right'], amount: int) -> None:
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
			event_result = await self.event_bus.expect(TabsInfoResponseEvent, timeout=5.0)
			response: TabsInfoResponseEvent = event_result  # type: ignore
			return response.tabs
		except TimeoutError:
			# No response received
			return []

	async def get_tabs_info_as_models(self) -> list[TabInfo]:
		"""Get information about all open tabs as TabInfo models."""
		tabs_data = await self.get_tabs_info()
		tab_infos = []

		for tab_dict in tabs_data:
			# Convert dictionary to TabInfo model
			tab_info = TabInfo(**tab_dict)  # Now the dict should have all required fields
			tab_infos.append(tab_info)

		return tab_infos

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
			event_result = await self.event_bus.expect(BrowserStateResponseEvent, timeout=60.0)
			response: BrowserStateResponseEvent = event_result  # type: ignore
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
		if page.url in NEW_TAB_URLS:
			return

		# Wait for page load (previously handled by NavigationWatchdog)
		await self._wait_for_page_load(page, timeout_overwrite)

	async def _wait_for_page_load(self, page: Page, timeout_overwrite: float | None = None) -> None:
		"""Wait for page to load completely with network idle."""
		timeout = timeout_overwrite or 30.0
		try:
			await page.wait_for_load_state('networkidle', timeout=timeout * 1000)
		except Exception:
			# If networkidle times out, at least wait for domcontentloaded
			try:
				await page.wait_for_load_state('domcontentloaded', timeout=10 * 1000)
			except Exception:
				pass  # Continue anyway

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
		tabs_info = await self.get_tabs_info_as_models()

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

		# Check for PDF viewer status from downloads watchdog
		is_pdf_viewer = False
		if self._downloads_watchdog:
			is_pdf_viewer = await self._downloads_watchdog.check_for_pdf_viewer(page)

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
			tabs_info = await self.get_tabs_info_as_models()
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
		if self._downloads_watchdog:
			try:
				is_pdf_viewer = await self._downloads_watchdog.check_for_pdf_viewer(page)
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
		"""Get selector map from the current browser state."""
		state = await self.get_browser_state_with_recovery()
		return state.selector_map if hasattr(state, 'selector_map') else {}

	async def get_dom_element_by_index(self, index: int) -> Any:
		"""Get DOM element by index from the selector map."""
		selector_map = await self.get_selector_map()
		return selector_map.get(index)

	async def find_file_upload_element_by_index(self, index: int) -> Any:
		"""Find file upload element by index."""
		element = await self.get_dom_element_by_index(index)
		if element and self.is_file_input(element):
			return element
		return None

	async def get_locate_element(self, element: Any) -> Any:
		"""Get playwright ElementHandle for a DOM element."""
		if not element or not hasattr(element, 'xpath'):
			return None

		page = await self.get_current_page()
		if not page:
			return None

		current_frame = page

		# Start with the target element and collect all parents
		parents = []
		current = element
		while hasattr(current, 'parent') and current.parent is not None:
			parent = current.parent
			parents.append(parent)
			current = parent

		# Reverse the parents list to process from top to bottom
		parents.reverse()

		# Process all iframe parents in sequence
		iframes = [item for item in parents if hasattr(item, 'tag_name') and item.tag_name == 'iframe']
		for parent in iframes:
			css_selector = self._enhanced_css_selector_for_element(
				parent,
				include_dynamic_attributes=self.browser_profile.include_dynamic_attributes,
			)
			# Use CSS selector if available, otherwise fall back to XPath
			if css_selector:
				current_frame = current_frame.frame_locator(css_selector)
			else:
				logger.debug(f'Using XPath for iframe: {parent.xpath}')
				current_frame = current_frame.frame_locator(f'xpath={parent.xpath}')

		css_selector = self._enhanced_css_selector_for_element(
			element, include_dynamic_attributes=self.browser_profile.include_dynamic_attributes
		)

		try:
			if hasattr(current_frame, 'locator'):
				if css_selector:
					element_handle = await current_frame.locator(css_selector).element_handle()
				else:
					# Fall back to XPath when CSS selector is empty
					logger.debug(f'CSS selector empty, falling back to XPath: {element.xpath}')
					element_handle = await current_frame.locator(f'xpath={element.xpath}').element_handle()
				return element_handle
			else:
				# FrameLocator doesn't have query_selector, use locator instead
				if css_selector:
					element_handle = await current_frame.locator(css_selector).element_handle()
				else:
					# Fall back to XPath
					logger.debug(f'CSS selector empty, falling back to XPath: {element.xpath}')
					element_handle = await current_frame.locator(f'xpath={element.xpath}').element_handle()
				if element_handle:
					is_visible = await self._is_visible(element_handle)
					if is_visible:
						await element_handle.scroll_into_view_if_needed(timeout=1_000)
					return element_handle
				return None
		except Exception as e:
			# If CSS selector failed, try XPath as fallback
			if css_selector and 'CSS.escape' not in str(e):
				try:
					logger.debug(f'CSS selector failed ({css_selector}), falling back to XPath: {element.xpath}')
					if hasattr(current_frame, 'locator'):
						element_handle = await current_frame.locator(f'xpath={element.xpath}').element_handle()
					else:
						element_handle = await current_frame.locator(f'xpath={element.xpath}').element_handle()
					if element_handle:
						is_visible = await self._is_visible(element_handle)
						if is_visible:
							await element_handle.scroll_into_view_if_needed(timeout=1_000)
						return element_handle
					return None
				except Exception:
					return None
			return None

	def is_file_input(self, element: Any) -> bool:
		"""Check if element is a file input."""
		if not element or not hasattr(element, 'tag_name') or not hasattr(element, 'attributes'):
			return False

		# Check if it's an input element with type="file"
		if element.tag_name.lower() == 'input':
			input_type = element.attributes.get('type', '').lower()
			if input_type == 'file':
				return True

		# Check for file-related attributes or classes that might indicate file upload
		# This is a more conservative check to avoid false positives
		return False

	async def _is_visible(self, element) -> bool:
		"""
		Checks if an element is visible on the page.
		We use our own implementation instead of relying solely on Playwright's is_visible() because
		of edge cases with CSS frameworks like Tailwind. When elements use Tailwind's 'hidden' class,
		the computed style may return display as '' (empty string) instead of 'none', causing Playwright
		to incorrectly consider hidden elements as visible. By additionally checking the bounding box
		dimensions, we catch elements that have zero width/height regardless of how they were hidden.
		"""
		is_hidden = await element.is_hidden()
		bbox = await element.bounding_box()
		is_visible = not is_hidden and bbox is not None and bbox['width'] > 0 and bbox['height'] > 0
		return is_visible

	@classmethod
	def _enhanced_css_selector_for_element(cls, element: Any, include_dynamic_attributes: bool = True) -> str:
		"""
		Creates a CSS selector for a DOM element, handling various edge cases and special characters.

		Args:
						element: The DOM element to create a selector for

		Returns:
						A valid CSS selector string
		"""
		try:
			# Get base selector from XPath
			css_selector = cls._convert_simple_xpath_to_css_selector(element.xpath)

			# Handle class attributes
			if 'class' in element.attributes and element.attributes['class'] and include_dynamic_attributes:
				# Define a regex pattern for valid class names in CSS
				import re

				valid_class_name_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]*$')

				# Iterate through the class attribute values
				classes = element.attributes['class'].split()
				for class_name in classes:
					# Skip empty class names
					if not class_name.strip():
						continue

					# Check if the class name is valid
					if valid_class_name_pattern.match(class_name):
						# Append the valid class name to the CSS selector
						css_selector += f'.{class_name}'
					else:
						# Skip invalid class names
						continue

			# Expanded set of safe attributes that are stable and useful for selection
			SAFE_ATTRIBUTES = {
				# Data attributes (if they're stable in your application)
				'id',
				# Standard HTML attributes
				'name',
				'type',
				'placeholder',
				# Accessibility attributes
				'role',
				'aria-label',
				'title',
				'alt',
			}

			for attr in SAFE_ATTRIBUTES:
				if attr in element.attributes and element.attributes[attr] and include_dynamic_attributes:
					value = element.attributes[attr]
					# Escape special characters in attribute values
					value = value.replace('"', '\\"')
					css_selector += f'[{attr}="{value}"]'

			return css_selector
		except Exception:
			# Fallback to empty string
			return ''

	@staticmethod
	def _convert_simple_xpath_to_css_selector(xpath: str) -> str:
		"""Converts simple XPath expressions to CSS selectors."""
		if not xpath:
			return ''

		# Remove leading slash if present
		xpath = xpath.lstrip('/')

		# Split into parts
		parts = xpath.split('/')
		css_parts = []

		for part in parts:
			if not part:
				continue

			# Handle custom elements with colons by escaping them
			if ':' in part and '[' not in part:
				base_part = part.replace(':', r'\:')
				css_parts.append(base_part)
				continue

			# Handle index notation [n]
			if '[' in part:
				base_part = part[: part.find('[')]
				# Handle custom elements with colons in the base part
				if ':' in base_part:
					base_part = base_part.replace(':', r'\:')
				index_part = part[part.find('[') :]

				# Handle multiple indices
				if '][' in index_part:
					# Multiple conditions, just use the element name
					css_parts.append(base_part)
				else:
					# Extract the index
					try:
						index = int(index_part[1:-1])  # Remove [ and ]
						# CSS uses 1-based :nth-child, XPath uses 1-based indexing
						css_parts.append(f'{base_part}:nth-child({index})')
					except ValueError:
						# Not a simple index, just use the element name
						css_parts.append(base_part)
			else:
				# Handle custom elements with colons
				if ':' in part:
					part = part.replace(':', r'\:')
				css_parts.append(part)

		return ' > '.join(css_parts) if css_parts else ''

	@staticmethod
	async def _get_unique_filename(directory: str | Path, filename: str) -> str:
		"""Generate a unique filename for downloads by appending (1), (2), etc., if a file already exists."""
		base, ext = os.path.splitext(filename)
		counter = 1
		new_filename = filename
		while os.path.exists(os.path.join(directory, new_filename)):
			new_filename = f'{base} ({counter}){ext}'
			counter += 1
		return new_filename

	async def _click_element_node(self, element: Any) -> str | None:
		"""Click an element node and handle potential downloads.

		Returns the download path if a download was triggered, None otherwise.
		"""
		if not element:
			raise ValueError('No element provided to click')

		page = await self.get_current_page()
		if not page:
			raise ValueError('No current page available')

		# Get the playwright locator for the element
		locator = await self.get_locate_element(element)
		if not locator:
			raise ValueError(f'Could not locate element with xpath: {element.xpath}')

		download_path = None

		# Handle potential downloads if downloads are enabled
		if self.browser_profile.downloads_path:
			try:
				# Try short-timeout expect_download to detect a file download
				async with page.expect_download(timeout=5_000) as download_info:
					await locator.click()
				download = await download_info.value

				# Determine file path
				suggested_filename = download.suggested_filename
				unique_filename = await self._get_unique_filename(self.browser_profile.downloads_path, suggested_filename)
				download_path = os.path.join(self.browser_profile.downloads_path, unique_filename)
				await download.save_as(download_path)
				logger.info(f'⬇️ Downloaded file to: {download_path}')

				# Emit download event for the watchdog
				self.event_bus.dispatch(
					FileDownloadedEvent(
						url=download.url,
						path=download_path,
						file_name=suggested_filename,
						file_size=os.path.getsize(download_path) if os.path.exists(download_path) else 0,
						file_type=os.path.splitext(suggested_filename)[1].lstrip('.'),
						mime_type=None,
						from_cache=False,
						auto_download=False,
					)
				)

			except Exception:
				# If no download is triggered, just perform normal click
				await locator.click()
		else:
			# If downloads are disabled, just perform the click
			await locator.click()

		return download_path

	async def _input_text_element_node(self, element: Any, text: str) -> None:
		"""Input text into an element node."""
		if not element:
			raise ValueError('No element provided for text input')

		page = await self.get_current_page()
		if not page:
			raise ValueError('No current page available')

		# Get the playwright locator for the element
		locator = await self.get_locate_element(element)
		if not locator:
			raise ValueError(f'Could not locate element with xpath: {element.xpath}')

		# Clear existing text and type new text
		await locator.fill(text)

	async def _scroll_with_cdp_gesture(self, page: Page, pixels: int) -> bool:
		"""
		Scroll using CDP Input.synthesizeScrollGesture for universal compatibility.

		Args:
			page: The page to scroll
			pixels: Number of pixels to scroll (positive = up, negative = down)

		Returns:
			True if successful, False if failed
		"""
		try:
			# Use CDP to synthesize scroll gesture - works in all contexts including PDFs
			cdp_session = await page.context.new_cdp_session(page)  # type: ignore

			# Get viewport center for scroll origin
			viewport = await page.evaluate("""
				() => ({
					width: window.innerWidth,
					height: window.innerHeight
				})
			""")

			center_x = viewport['width'] // 2
			center_y = viewport['height'] // 2

			await cdp_session.send(
				'Input.synthesizeScrollGesture',
				{
					'x': center_x,
					'y': center_y,
					'xDistance': 0,
					'yDistance': -pixels,  # Negative = scroll down, Positive = scroll up
					'gestureSourceType': 'mouse',  # Use mouse gestures for better compatibility
					'speed': 3000,  # Pixels per second
				},
			)

			try:
				await asyncio.wait_for(cdp_session.detach(), timeout=1.0)
			except (TimeoutError, Exception):
				# Detach may timeout on some CDP implementations
				pass

			return True
		except Exception as e:
			logger.debug(f'CDP scroll gesture failed: {e}')
			return False

	async def _scroll_container(self, pixels: int) -> None:
		"""Scroll using CDP gesture synthesis with JavaScript fallback."""

		page = await self.get_current_page()

		# Try CDP scroll gesture first (works universally including PDFs)
		if await self._scroll_with_cdp_gesture(page, pixels):
			return

		# Fallback to JavaScript for older browsers or when CDP fails
		logger.debug('Falling back to JavaScript scrolling')
		SMART_SCROLL_JS = """(dy) => {
			const bigEnough = el => el.clientHeight >= window.innerHeight * 0.5;
			const canScroll = el =>
				el &&
				/(auto|scroll|overlay)/.test(getComputedStyle(el).overflowY) &&
				el.scrollHeight > el.clientHeight &&
				bigEnough(el);

			let el = document.activeElement;
			while (el && !canScroll(el) && el !== document.body) el = el.parentElement;

			el = canScroll(el)
					? el
					: [...document.querySelectorAll('*')].find(canScroll)
					|| document.scrollingElement
					|| document.documentElement;

			if (el === document.scrollingElement ||
				el === document.documentElement ||
				el === document.body) {
				window.scrollBy(0, dy);
			} else {
				el.scrollBy({ top: dy, behavior: 'auto' });
			}
		}"""
		await page.evaluate(SMART_SCROLL_JS, pixels)

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

	# ========== Helper Methods ==========

	def _add_page_to_watchdogs(self, page: Page) -> None:
		"""Add a page to all active watchdogs."""
		# Add page to crash watchdog
		if hasattr(self, '_crash_watchdog') and self._crash_watchdog:
			self._crash_watchdog.add_page(page)

		# Add page to downloads watchdog
		if hasattr(self, '_downloads_watchdog') and self._downloads_watchdog:
			self._downloads_watchdog.add_page(page)

		# Add page to navigation tracking
		self._add_page_tracking(page)

	# ========== Navigation Helper Methods (merged from NavigationWatchdog) ==========

	def _add_page_tracking(self, page: Page) -> None:
		"""Add a page to track for navigation."""
		self._tracked_pages.add(page)
		self._setup_page_listeners(page)
		logger.debug(f'[Session] Added page to navigation tracking: {page.url}')

	def _setup_page_listeners(self, page: Page) -> None:
		"""Set up network request listeners for a page."""
		# Initialize tracking structures
		self._page_requests[page] = set()
		self._page_last_activity[page] = asyncio.get_event_loop().time()

		# Set up request/response tracking
		page.on('request', lambda request: self._on_request(page, request))
		page.on('response', lambda response: self._on_response(page, response))
		page.on('requestfailed', lambda request: self._on_request_failed(page, request))
		page.on('requestfinished', lambda request: self._on_request_finished(page, request))

	def _on_request(self, page: Page, request: Request) -> None:
		"""Track new network request."""
		if page in self._page_requests:
			self._page_requests[page].add(request)
			self._page_last_activity[page] = asyncio.get_event_loop().time()
			logger.debug(f'[Session] Request started: {request.method} {request.url}')

	def _on_response(self, page: Page, response: Response) -> None:
		"""Track network response."""
		if page in self._page_requests and response.request in self._page_requests[page]:
			self._page_requests[page].discard(response.request)
			self._page_last_activity[page] = asyncio.get_event_loop().time()
			logger.debug(f'[Session] Request completed: {response.request.method} {response.url}')

	def _on_request_failed(self, page: Page, request: Request) -> None:
		"""Handle failed network request."""
		if page in self._page_requests:
			self._page_requests[page].discard(request)
			logger.debug(f'[Session] Request failed: {request.method} {request.url}')

	def _on_request_finished(self, page: Page, request: Request) -> None:
		"""Handle finished network request."""
		if page in self._page_requests:
			self._page_requests[page].discard(request)
			logger.debug(f'[Session] Request finished: {request.method} {request.url}')

	async def _monitor_page_network(self, page: Page) -> None:
		"""Monitor network activity for a page after navigation."""
		# Cancel any existing monitoring task for this page
		if page in self._monitoring_tasks:
			old_task = self._monitoring_tasks[page]
			if not old_task.done():
				old_task.cancel()

		# Skip monitoring for new tab pages
		if page.url in NEW_TAB_URLS:
			return

		# Ensure page is tracked
		if page not in self._tracked_pages:
			self._add_page_tracking(page)

		# Create new monitoring task
		task = asyncio.create_task(self._wait_for_stable_network(page))
		self._monitoring_tasks[page] = task

	async def _wait_for_stable_network(self, page: Page) -> None:
		"""Wait for network to be stable with no pending requests for a specific page."""
		logger.info(f'[Session] Monitoring network stability for {page.url}')

		start_time = asyncio.get_event_loop().time()

		try:
			while True:
				await asyncio.sleep(0.1)
				now = asyncio.get_event_loop().time()

				# Get pending requests for this page
				pending_requests = self._page_requests.get(page, set())
				last_activity = self._page_last_activity.get(page, start_time)

				# Check if network is idle
				idle_time = self.browser_profile.wait_for_network_idle_page_load_time
				if len(pending_requests) == 0 and (now - last_activity) >= idle_time:
					# Page loaded successfully
					logger.info(f'[Session] Network stable for {page.url}')
					break

				# Check for timeout
				max_wait = self.browser_profile.maximum_wait_page_load_time
				if now - start_time > max_wait:
					logger.info(
						f'[Session] Network timeout after {max_wait}s with {len(pending_requests)} '
						f'pending requests for {page.url}'
					)

					# Dispatch NavigationCompleteEvent with loading status
					loading_status = (
						f'Page loading was aborted after {max_wait}s with {len(pending_requests)} pending network requests. '
						f'You may want to use the wait action to allow more time for the page to fully load.'
					)

					tab_index = await self._get_tab_index(page)
					self.event_bus.dispatch(
						NavigationCompleteEvent(
							tab_index=tab_index,
							url=page.url,
							status=None,
							error_message=f'Network timeout with {len(pending_requests)} pending requests',
							loading_status=loading_status,
						)
					)
					break

		except asyncio.CancelledError:
			logger.debug(f'[Session] Network monitoring cancelled for {page.url}')
			raise
		except Exception as e:
			logger.error(f'[Session] Error monitoring network: {e}')

	async def _get_page_for_navigation(self, event: NavigateToUrlEvent) -> Page | None:
		"""Get the page to navigate based on the event."""
		# Use agent focus watchdog to get current page
		if hasattr(self, '_agent_focus_watchdog') and self._agent_focus_watchdog:
			try:
				return await self._agent_focus_watchdog.get_or_create_page()
			except Exception:
				pass

		# Fallback to first page
		if hasattr(self, '_browser_context') and self._browser_context:
			pages = self._browser_context.pages
			if pages:
				return pages[0]

		return None

	async def _get_tab_index(self, page: Page) -> int:
		"""Get the tab index for a page."""
		if hasattr(self, '_browser_context') and self._browser_context:
			pages = self._browser_context.pages
			if page in pages:
				return pages.index(page)
		return 0

	def _get_pending_requests(self, page: Page) -> set[Request]:
		"""Get pending requests for a page."""
		return self._page_requests.get(page, set()).copy()

	def _is_page_loading(self, page: Page) -> bool:
		"""Check if a page has pending network requests."""
		return len(self._page_requests.get(page, set())) > 0

	async def wait_for_page_load(self, page: Page, timeout: float | None = None) -> None:
		"""Wait for a page to finish loading with stable network.

		This method can be called externally to wait for page load completion.
		"""
		# Skip wait for new tab pages
		if page.url in NEW_TAB_URLS:
			return

		# If page not tracked, add it
		if page not in self._tracked_pages:
			self._add_page_tracking(page)

		# Wait for stable network
		await self._wait_for_stable_network(page)

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

	def _dispatch_no_page_error(self, error_type: str, message: str) -> None:
		"""Dispatch a standard 'no active page' error event."""
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type=error_type,
				message=message,
			)
		)


# Import uuid7str for ID generation
try:
	from uuid_extensions import uuid7str
except ImportError:
	import uuid

	def uuid7str() -> str:
		return str(uuid.uuid4())
