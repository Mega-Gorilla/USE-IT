"""Event-driven browser session with backwards compatibility."""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from bubus import EventBus
from bubus.helpers import retry
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str

from browser_use.browser.events import (
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserLaunchEvent,
	BrowserStartEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.views import (
	BrowserStateSummary,
	PageInfo,
	TabInfo,
)
from browser_use.observability import observe_debug
from browser_use.utils import (
	_log_pretty_url,
	is_new_tab_page,
	logger,
	time_execution_async,
)

if TYPE_CHECKING:
	from cdp_use import CDPClient

	from browser_use.dom.views import EnhancedDOMTreeNode

_GLOB_WARNING_SHOWN = False  # used inside _is_url_allowed to avoid spamming the logs with the same warning multiple times

MAX_SCREENSHOT_HEIGHT = 2000
MAX_SCREENSHOT_WIDTH = 1920


def _log_glob_warning(domain: str, glob: str, logger: logging.Logger):
	global _GLOB_WARNING_SHOWN
	if not _GLOB_WARNING_SHOWN:
		logger.warning(
			# glob patterns are very easy to mess up and match too many domains by accident
			# e.g. if you only need to access gmail, don't use *.google.com because an attacker could convince the agent to visit a malicious doc
			# on docs.google.com/s/some/evil/doc to set up a prompt injection attack
			f"⚠️ Allowing agent to visit {domain} based on allowed_domains=['{glob}', ...]. Set allowed_domains=['{domain}', ...] explicitly to avoid matching too many domains!"
		)
		_GLOB_WARNING_SHOWN = True


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
	id: str = Field(default_factory=lambda: uuid7str())
	browser_profile: BrowserProfile = Field(default_factory=lambda: DEFAULT_BROWSER_PROFILE)

	# Connection info (for backwards compatibility)
	cdp_url: str | None = None
	is_local: bool = Field(default=True)

	# Mutable state
	current_target_id: str | None = None
	"""Current active target ID for the main page"""

	# Event bus
	event_bus: EventBus = Field(default_factory=EventBus)

	# PDF handling
	_auto_download_pdfs: bool = PrivateAttr(default=True)

	def model_post_init(self, __context) -> None:
		"""Register event handlers after model initialization."""
		# Register BrowserSession's event handlers manually since it's not a BaseWatchdog
		self.event_bus.on('BrowserStartEvent', self.on_BrowserStartEvent)
		self.event_bus.on('BrowserStopEvent', self.on_BrowserStopEvent)

	# Watchdogs
	_crash_watchdog: Any = PrivateAttr(default=None)
	_downloads_watchdog: Any = PrivateAttr(default=None)
	_aboutblank_watchdog: Any = PrivateAttr(default=None)
	_navigation_watchdog: Any = PrivateAttr(default=None)
	_storage_state_watchdog: Any = PrivateAttr(default=None)
	_local_browser_watchdog: Any = PrivateAttr(default=None)
	_default_action_watchdog: Any = PrivateAttr(default=None)
	_dom_watchdog: Any = PrivateAttr(default=None)

	# Navigation tracking now handled by watchdogs

	# Cached browser state for synchronous access
	_cached_browser_state_summary: Any = PrivateAttr(default=None)
	_cached_selector_map: dict[int, 'EnhancedDOMTreeNode'] = PrivateAttr(default_factory=dict)
	"""Cached mapping of element indices to DOM nodes"""

	# CDP client
	_cdp_client: 'CDPClient | None' = PrivateAttr(default=None)
	"""Cached CDP client instance"""

	_logger: Any = PrivateAttr(default=None)

	@property
	def logger(self) -> Any:
		"""Get instance-specific logger with session ID in the name"""
		if (
			self._logger is None or self._browser_context is None
		):  # keep updating the name pre-init because our id and str(self) can change
			import logging

			self._logger = logging.getLogger(f'browser_use.{self}')
		return self._logger

	@property
	def cdp_client(self) -> 'CDPClient | None':
		"""Get the cached CDP client if it exists.

		The client is created and started in setup_browser_via_cdp_url().

		Returns:
			The CDP client instance or None if not yet created
		"""
		return self._cdp_client

	def __repr__(self) -> str:
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0]
		return f'BrowserSession🆂 {self.id[-4:]}:{port_number} #{str(id(self))[-2:]} (cdp_url={self.cdp_url}, profile={self.browser_profile})'

	def __str__(self) -> str:
		# Note: _original_browser_session tracking moved to Agent class
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0]
		return (
			f'BrowserSession🆂 {self.id[-4:]}:{port_number} #{str(id(self))[-2:]}'  # ' 🅟 {str(id(self.current_target_id))[-2:]}'
		)

	async def on_BrowserStartEvent(self, event: BrowserStartEvent) -> None:
		"""Handle browser start request."""

		# Initialize and attach all watchdogs FIRST so LocalBrowserWatchdog can handle BrowserLaunchEvent
		await self.attach_all_watchdogs()

		if self.cdp_url and self._browser_context:
			self.event_bus.dispatch(BrowserConnectedEvent(cdp_url=self.cdp_url))
			return

		try:
			if self.is_local and not self.cdp_url:
				# Launch local browser using event-driven approach
				launch_event = self.event_bus.dispatch(BrowserLaunchEvent())
				await launch_event

				# Get the CDP URL from LocalBrowserWatchdog handler result
				results = await launch_event.event_results_flat_dict()
				self.cdp_url = results.get('cdp_url')

				if not self.cdp_url:
					raise Exception('No CDP URL returned from LocalBrowserWatchdog')

			assert self.cdp_url and '://' in self.cdp_url

			# Setup browser via CDP without Playwright
			await self.setup_browser_via_cdp_url()

			# Notify that browser is connected
			self.event_bus.dispatch(BrowserConnectedEvent(cdp_url=self.cdp_url))

		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='BrowserStartEventError',
					message=f'Failed to start browser: {type(e).__name__} {e}',
					details={'cdp_url': self.cdp_url, 'is_local': self.is_local},
				)
			)
			raise

	async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
		"""Handle browser stop request."""

		try:
			# TODO: close all pages here or tell the browser to close gracefully? is there any point?
			# we might need to give the browser time to save trace files, recordings, etc. during shutdown

			# Check if we should keep the browser alive
			if self.browser_profile.keep_alive and not event.force:
				self.event_bus.dispatch(BrowserStoppedEvent(reason='Kept alive due to keep_alive=True'))
				return

			# Reset state
			self._browser = None
			self._browser_context = None
			if self.is_local:
				self.cdp_url = None

			# Notify stop and wait for all handlers to complete
			# LocalBrowserWatchdog listens for BrowserStopEvent and dispatches BrowserKillEvent
			stop_event = self.event_bus.dispatch(BrowserStoppedEvent(reason='Stopped by request'))
			await stop_event

		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='BrowserStopEventError',
					message=f'Failed to stop browser: {type(e).__name__} {e}',
					details={'cdp_url': self.cdp_url, 'is_local': self.is_local},
				)
			)

	# ========== Helper Methods ==========

	async def get_browser_state_with_recovery(
		self, cache_clickable_elements_hashes: bool = True, include_screenshot: bool = False
	) -> 'BrowserStateSummary':
		"""Get browser state using event system.

		This is a compatibility wrapper that dispatches BrowserStateRequestEvent.

		Args:
			cache_clickable_elements_hashes: Whether to cache element hashes (for compatibility)
			include_screenshot: Whether to include screenshot in state

		Returns:
			BrowserStateSummary from the event handler
		"""
		from browser_use.browser.events import BrowserStateRequestEvent

		# Dispatch the event and wait for result
		event = self.event_bus.dispatch(
			BrowserStateRequestEvent(
				include_dom=True,
				include_screenshot=include_screenshot,
				cache_clickable_elements_hashes=cache_clickable_elements_hashes,
			)
		)

		# The handler returns the BrowserStateSummary directly
		result = await event.event_result()
		return result

	async def get_state_summary(self, cache_clickable_elements_hashes: bool = True) -> 'BrowserStateSummary':
		"""Alias for get_browser_state_with_recovery for backwards compatibility."""
		return await self.get_browser_state_with_recovery(
			cache_clickable_elements_hashes=cache_clickable_elements_hashes, include_screenshot=False
		)

	async def attach_all_watchdogs(self) -> None:
		"""Initialize and attach all watchdogs in one go."""
		from browser_use.browser.aboutblank_watchdog import AboutBlankWatchdog
		from browser_use.browser.crash_watchdog import CrashWatchdog
		from browser_use.browser.default_action_watchdog import DefaultActionWatchdog
		from browser_use.browser.dom_watchdog import DOMWatchdog
		from browser_use.browser.downloads_watchdog import DownloadsWatchdog
		from browser_use.browser.local_browser_watchdog import LocalBrowserWatchdog
		from browser_use.browser.navigation_watchdog import NavigationWatchdog
		from browser_use.browser.storage_state_watchdog import StorageStateWatchdog

		watchdog_configs = [
			('_crash_watchdog', CrashWatchdog),
			('_downloads_watchdog', DownloadsWatchdog),
			('_storage_state_watchdog', StorageStateWatchdog),
			('_local_browser_watchdog', LocalBrowserWatchdog),
			('_navigation_watchdog', NavigationWatchdog),
			('_aboutblank_watchdog', AboutBlankWatchdog),
			('_default_action_watchdog', DefaultActionWatchdog),
			('_dom_watchdog', DOMWatchdog),
		]

		for attr_name, watchdog_class in watchdog_configs:
			if not hasattr(self, attr_name) or getattr(self, attr_name) is None:
				try:
					watchdog = watchdog_class(event_bus=self.event_bus, browser_session=self)
					await watchdog.attach_to_session()
					setattr(self, attr_name, watchdog)
					# logger.debug(f'[Session] Initialized and attached {watchdog_class.__name__}')
				except Exception as e:
					logger.warning(f'[Session] Failed to initialize {watchdog_class.__name__}: {e}')
			else:
				# Watchdog already exists, don't re-initialize to avoid duplicate handlers
				logger.debug(f'[Session] {watchdog_class.__name__} already initialized, skipping')

	def model_copy(self, **kwargs) -> Self:
		"""Create a copy of this BrowserSession that shares the browser resources but doesn't own them.

		This method creates a copy that:
		- Shares the same browser, browser_context, and playwright objects
		- Doesn't own the browser resources (won't close them when garbage collected)
		- Keeps a reference to the original to prevent premature garbage collection
		"""
		# Create the copy using the parent class method
		copy = super().model_copy(**kwargs)

		# The copy doesn't own the browser resources
		copy._owns_browser_resources = False

		# Note: _original_browser_session tracking moved to Agent class
		# Keep the copy without reference to avoid circular dependencies

		# Manually copy over the excluded fields that are needed for browser connection
		# These fields are excluded in the model config but need to be shared
		copy.current_target_id = self.current_target_id

		return copy

	async def setup_browser_via_cdp_url(self) -> None:
		"""Connect to a remote chromium-based browser via CDP using cdp-use."""

		if not self.cdp_url:
			return  # no cdp_url provided, nothing to do

		self.logger.info(f'🌎 Connecting to existing chromium-based browser via CDP: {self.cdp_url} -> (remote browser)')

		# Import cdp-use client
		import httpx
		from cdp_use import CDPClient

		# Convert HTTP URL to WebSocket URL if needed
		ws_url = self.cdp_url
		if not ws_url.startswith('ws'):
			# If it's an HTTP URL, fetch the WebSocket URL from /json/version endpoint
			url = ws_url.rstrip('/')
			if not url.endswith('/json/version'):
				url = url + '/json/version'
			async with httpx.AsyncClient() as client:
				version_info = await client.get(url)
				ws_url = version_info.json()['webSocketDebuggerUrl']

		# Create and store the CDP client for direct CDP communication
		if self._cdp_client is None:
			self._cdp_client = CDPClient(ws_url)
			await self._cdp_client.start()

		# Get browser targets to find available contexts/pages
		targets = await self._cdp_client.send.Target.getTargets()

		# Find main browser pages (not iframes or workers)
		page_targets = [t for t in targets['targetInfos'] if t['type'] == 'page']

		if not page_targets:
			# No pages found, create a new one
			new_target = await self._cdp_client.send.Target.createTarget(params={'url': 'about:blank'})
			target_id = new_target['targetId']
		else:
			# Use the first available page
			target_id = page_targets[0]['targetId']

		# Store the current page target ID
		self.current_target_id = target_id

		# Mark that we're connected via CDP (no playwright browser object)
		self._cdp_connected = True

	async def setup_domservice_init_scripts(self, retry_count: int = 0) -> None:
		# self.logger.debug('Setting up init scripts in browser')

		init_script = """
			// check to make sure we're not inside the PDF viewer
			window.isPdfViewer = !!document?.body?.querySelector('body > embed[type="application/pdf"][width="100%"]')
			if (!window.isPdfViewer) {

				// Permissions
				const originalQuery = window.navigator.permissions.query;
				window.navigator.permissions.query = (parameters) => (
					parameters.name === 'notifications' ?
						Promise.resolve({ state: Notification.permission }) :
						originalQuery(parameters)
				);
				(() => {
					if (window._eventListenerTrackerInitialized) return;
					window._eventListenerTrackerInitialized = true;

					const originalAddEventListener = EventTarget.prototype.addEventListener;
					const eventListenersMap = new WeakMap();

					EventTarget.prototype.addEventListener = function(type, listener, options) {
						if (typeof listener === "function") {
							let listeners = eventListenersMap.get(this);
							if (!listeners) {
								listeners = [];
								eventListenersMap.set(this, listeners);
							}

							listeners.push({
								type,
								listener,
								listenerPreview: listener.toString().slice(0, 100),
								options
							});
						}

						return originalAddEventListener.call(this, type, listener, options);
					};

					window.getEventListenersForNode = (node) => {
						const listeners = eventListenersMap.get(node) || [];
						return listeners.map(({ type, listenerPreview, options }) => ({
							type,
							listenerPreview,
							options
						}));
					};
				})();
			}
		"""
		# TODO: convert this to pure cdp-use and/or move it to the dom_watchdog.py
		# await self.browser_context.add_init_script(init_script)

	@property
	async def target_ids(self) -> list[str]:
		"""Get all open page target IDs using CDP."""
		try:
			pages = await self._cdp_get_all_pages()
			return [page['targetId'] for page in pages]
		except Exception:
			return []

	async def get_target_id_by_tab_index(self, tab_index: int) -> str | None:
		"""Get target ID by tab index."""
		target_ids = await self.target_ids
		if 0 <= tab_index < len(target_ids):
			return target_ids[tab_index]
		return None

	async def get_tab_index(self, target_id: str) -> int:
		"""Get tab index for a target ID."""
		target_ids = await self.target_ids
		if target_id in target_ids:
			return target_ids.index(target_id)
		return -1

	async def get_tabs_info(self) -> list[TabInfo]:
		"""Get information about all open tabs using CDP for reliability."""
		tabs = []

		# Get all page targets using CDP
		pages = await self._cdp_get_all_pages()
		for i, page_target in enumerate(pages):
			target_id = page_target['targetId']
			url = page_target['url']

			# Skip JS execution for chrome:// pages and new tab pages
			if is_new_tab_page(url) or url.startswith('chrome://'):
				# Use URL as title for chrome pages, or mark new tabs as unusable
				if is_new_tab_page(url):
					title = 'ignore this tab and do not use it'
				else:
					# For chrome:// pages, use the URL itself as the title
					title = url
			else:
				# Normal pages - try to get title with CDP for reliability
				try:
					# Use helper to get document title
					title_result = await asyncio.wait_for(
						self._cdp_execute_on_target(target_id, commands=[('Runtime.evaluate', {'expression': 'document.title'})]),
						timeout=2.0,
					)
					title = title_result.get('result', {}).get('value', '') if title_result else ''

					# Special handling for PDF pages
					if (not title or title == '') and (url.endswith('.pdf') or 'pdf' in url):
						# PDF pages might not have a title, use URL filename
						try:
							from urllib.parse import urlparse

							filename = urlparse(url).path.split('/')[-1]
							if filename:
								title = filename
						except Exception:
							pass

				except Exception as e:
					# Page might be crashed or unresponsive
					self.logger.debug(f'⚠️ Failed to get tab info for tab #{i}: {_log_pretty_url(url)} - {type(e).__name__}')

					# Only mark as unusable if it's actually a new tab page
					if is_new_tab_page(url):
						title = 'ignore this tab and do not use it'
					else:
						# For crashed pages, close them as they're not useful
						try:
							await self._cdp_close_page(target_id)
							self.logger.debug(f"🪓 Force-closed page because it's unresponsive: {_log_pretty_url(url)}")
							continue
						except Exception:
							title = '(Error)'

			tab_info = TabInfo(
				page_id=i,
				url=url,
				title=title,
				parent_page_id=None,
				id=target_id,  # Use target ID as the unique identifier
				index=i,
			)
			tabs.append(tab_info)
		return tabs

	# DOM element methods
	# Removed duplicate get_browser_state_with_recovery - using the decorated version below

	@observe_debug(ignore_input=True, ignore_output=True, name='get_minimal_state_summary')
	@time_execution_async('--get_minimal_state_summary')
	async def get_minimal_state_summary(self) -> BrowserStateSummary:
		"""Get basic page info without DOM processing, but try to capture screenshot"""
		from browser_use.browser.views import BrowserStateSummary
		from browser_use.dom.views import EnhancedDOMTreeNode as DOMElementNode
		from browser_use.dom.views import NodeType, SerializedDOMState

		# Get basic info - no DOM parsing to avoid errors
		url = await self.get_current_page_url() or 'unknown'

		# Try to get title safely
		try:
			# timeout after 2 seconds
			title = await asyncio.wait_for(self.get_current_page_title(), timeout=2.0)
		except Exception:
			title = 'Page Load Error'

		# Try to get tabs info safely
		try:
			# timeout after 2 seconds
			tabs_info = await retry(timeout=2, retries=0)(self.get_tabs_info)()
		except Exception:
			tabs_info = []

		# Create minimal DOM element for error state
		minimal_element_tree = DOMElementNode(
			node_id=1,
			backend_node_id=1,
			node_type=NodeType.ELEMENT_NODE,
			node_name='body',
			node_value='',
			attributes={},
			is_scrollable=False,
			is_visible=True,
			absolute_position=None,
			frame_id=None,
			target_id=self.current_target_id,
			content_document=None,
			shadow_root_type=None,
			shadow_roots=None,
			parent_node=None,
			children_nodes=[],
			ax_node=None,
			snapshot_node=None,
		)

		# Check if current page is a PDF viewer
		is_pdf_viewer = await self._is_pdf_viewer(page)

		# Create minimal SerializedDOMState
		minimal_dom_state = SerializedDOMState(
			_root=None,  # No simplified tree for minimal state
			selector_map={},  # Empty selector map
		)

		return BrowserStateSummary(
			dom_state=minimal_dom_state,
			url=url,
			title=title,
			tabs=tabs_info,
			pixels_above=0,
			pixels_below=0,
			browser_errors=[f'Page state retrieval failed, minimal recovery applied for {url}'],
			is_pdf_viewer=is_pdf_viewer,
			recent_events='',
		)

	@observe_debug(ignore_input=True, ignore_output=True, name='get_updated_state')
	async def _get_updated_state(self, focus_element: int = -1, include_screenshot: bool = True) -> BrowserStateSummary:
		"""Update and return state."""

		# Get current page URL
		page_url = await self.get_current_page_url()

		# Check if this is a new tab or chrome:// page early for optimization
		is_empty_page = is_new_tab_page(page_url) or page_url.startswith('chrome://')

		try:
			# Fast path for empty pages - skip all expensive operations
			if is_empty_page:
				self.logger.debug(f'⚡ Fast path for empty page: {page_url}')

				# Create minimal DOM state immediately - just return None for now
				# since DOM classes have been refactored
				content = None

				# Get tabs info
				tabs_info = await self.get_tabs_info()

				# Skip screenshot for empty pages
				screenshot_b64 = None

				# Use default viewport dimensions from browser profile
				viewport = self.browser_profile.viewport or {'width': 1280, 'height': 720}
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

				# Return minimal state immediately
				self.browser_state_summary = BrowserStateSummary(
					dom_state=content,
					url=page_url,
					title='New Tab' if is_new_tab_page(page_url) else 'Chrome Page',
					tabs=tabs_info,
					screenshot=screenshot_b64,
					page_info=page_info,
					pixels_above=0,
					pixels_below=0,
					browser_errors=[],
					is_pdf_viewer=False,
				)
				return self.browser_state_summary

			# Normal path for regular pages
			self.logger.debug('🧹 Removing highlights...')
			try:
				await self.remove_highlights()
			except TimeoutError:
				self.logger.debug('Timeout to remove highlights')

			# Check for PDF and auto-download if needed
			try:
				pdf_path = await self._auto_download_pdf_if_needed(page)
				if pdf_path:
					self.logger.info(f'📄 PDF auto-downloaded: {pdf_path}')
			except Exception as e:
				self.logger.debug(f'PDF auto-download check failed: {type(e).__name__}: {e}')

			self.logger.debug('🌳 Starting DOM processing...')
			from browser_use.browser.events import BuildDOMTreeEvent
			from browser_use.dom.views import SerializedDOMState

			try:
				# Use the DOMWatchdog via event bus
				result = await asyncio.wait_for(
					self.event_bus.dispatch(BuildDOMTreeEvent(previous_state=None)),
					timeout=45.0,  # 45 second timeout for DOM processing - generous for complex pages
				)
				content = await result.event_result()
				self.logger.debug('✅ DOM processing completed')
			except (TimeoutError, Exception) as e:
				if isinstance(e, TimeoutError):
					self.logger.warning(f'DOM processing timed out after 45 seconds for {page_url}')
				else:
					self.logger.warning(f'DOM processing failed: {e}')
				self.logger.warning('🔄 Falling back to minimal DOM state to allow basic navigation...')

				# Create minimal DOM state for basic navigation
				content = SerializedDOMState(
					_root=None,  # No simplified tree for minimal state
					selector_map={},  # Empty selector map
				)

			self.logger.debug('📋 Getting tabs info...')
			tabs_info = await self.get_tabs_info()
			self.logger.debug('✅ Tabs info completed')

			# Get all cross-origin iframes within the page and open them in new tabs
			# mark the titles of the new tabs so the LLM knows to check them for additional content
			# unfortunately too buggy for now, too many sites use invisible cross-origin iframes for ads, tracking, youtube videos, social media, etc.
			# and it distracts the bot by opening a lot of new tabs
			# iframe_urls = await dom_service.get_cross_origin_iframes()
			# outer_page = self.current_target_id
			# for url in iframe_urls:
			# 	if url in [tab.url for tab in tabs_info]:
			# 		continue  # skip if the iframe if we already have it open in a tab
			# 	new_page_id = tabs_info[-1].page_id + 1
			# 	self.logger.debug(f'Opening cross-origin iframe in new tab #{new_page_id}: {url}')
			# 	await self.create_new_tab(url)
			# 	tabs_info.append(
			# 		TabInfo(
			# 			page_id=new_page_id,
			# 			url=url,
			# 			title=f'iFrame opened as new tab, treat as if embedded inside page {outer_page.url}: {page.url}',
			# 			parent_page_url=outer_page.url,
			# 		)
			# 	)

			if include_screenshot:
				try:
					self.logger.debug('📸 Capturing screenshot...')
					# Reasonable timeout for screenshot
					screenshot_b64 = await self.take_screenshot()
					# self.logger.debug('✅ Screenshot completed')
				except Exception as e:
					self.logger.warning(f'❌ Screenshot failed for {_log_pretty_url(page.url)}: {type(e).__name__} {e}')
					screenshot_b64 = None
			else:
				screenshot_b64 = None

			# Get comprehensive page information
			page_info = await self.get_page_info(page)
			try:
				self.logger.debug('📏 Getting scroll info...')
				pixels_above, pixels_below = await asyncio.wait_for(self.get_scroll_info(page), timeout=5.0)
				self.logger.debug('✅ Scroll info completed')
			except Exception as e:
				self.logger.warning(f'Failed to get scroll info: {type(e).__name__}')
				pixels_above, pixels_below = 0, 0

			try:
				title = await asyncio.wait_for(self.get_current_page_title(), timeout=3.0)
			except Exception:
				title = 'Title unavailable'

			# Check if this is a minimal fallback state
			browser_errors = []
			if not content.selector_map:  # Empty selector map indicates fallback state
				browser_errors.append(
					f'DOM processing timed out for {page_url} - using minimal state. Basic navigation still available via go_to_url, scroll, and search actions.'
				)

			# Check if current page is a PDF viewer
			is_pdf_viewer = await self._is_pdf_viewer(page)

			self.browser_state_summary = BrowserStateSummary(
				dom_state=content,
				url=page_url,
				title=title,
				tabs=tabs_info,
				screenshot=screenshot_b64,
				page_info=page_info,
				pixels_above=pixels_above,
				pixels_below=pixels_below,
				browser_errors=browser_errors,
				is_pdf_viewer=is_pdf_viewer,
			)

			self.logger.debug('✅ get_state_summary completed successfully')
			return self.browser_state_summary
		except Exception as e:
			self.logger.error(f'❌ Failed to update browser_state_summary: {type(e).__name__}: {e}')
			# Return last known good state if available
			if hasattr(self, 'browser_state_summary'):
				return self.browser_state_summary
			raise

	# ========== CDP Helper Methods ==========

	async def cdp_clients_for_target(self, target_id: str) -> list['CDPClient']:
		"""Get CDP clients for a target, including main and iframe sessions.

		Returns list with root target session first, then iframe sessions.
		"""
		if not self.cdp_client:
			raise ValueError('CDP client not initialized')

		clients = []

		# Attach to main target
		session = await self.cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
		session_id = session['sessionId']

		# For now, return just the main client with session
		# In future, we'd enumerate iframes and attach to them too
		clients.append(self.cdp_client)  # Root client with session attached

		return clients

	async def cdp_client_for_frame(self, frame_id: str) -> 'CDPClient':
		"""Get CDP client for a specific frame ID."""
		# For now, return the main client
		# In future, we'd look up which session owns this frame
		return self.cdp_client

	async def cdp_client_for_node(self, node: 'EnhancedDOMTreeNode') -> 'CDPClient':
		"""Get CDP client for a specific DOM node based on its frame."""
		if node.frame_id:
			return await self.cdp_client_for_frame(node.frame_id)
		return self.cdp_client

	async def frames_by_target(self, target_id: str) -> list[str]:
		"""Get all frame IDs for a target."""
		# Get frame tree using helper
		frame_tree = await self._cdp_execute_on_target(target_id, commands=[('Page.getFrameTree', {})])

		# Extract frame IDs recursively
		frame_ids = []

		def extract_frames(tree_node):
			frame_ids.append(tree_node['frame']['id'])
			for child in tree_node.get('childFrames', []):
				extract_frames(child)

		extract_frames(frame_tree['frameTree'])

		return frame_ids

	async def target_id_by_frame_id(self, frame_id: str) -> str | None:
		"""Get target ID for a given frame ID.

		Note: This requires iterating through all targets to find the frame.
		"""
		targets = await self.cdp_client.send.Target.getTargets()

		for target in targets['targetInfos']:
			if target['type'] in ['page', 'iframe']:
				# Check if this target contains the frame
				frames = await self.frames_by_target(target['targetId'])
				if frame_id in frames:
					return target['targetId']

		return None

	async def get_current_page_cdp_session_id(self) -> str | None:
		"""Get the CDP session ID for the current page."""
		if not hasattr(self, 'current_target_id') or not self.current_target_id:
			return None

		# Attach to the current target and get session ID
		session = await self.cdp_client.send.Target.attachToTarget(params={'targetId': self.current_target_id, 'flatten': True})
		return session['sessionId']

	async def _create_fresh_cdp_client(self) -> Any:
		"""Create a new CDP client instance. Caller is responsible for cleanup."""
		if not self.cdp_url:
			raise ValueError('CDP URL is not set')

		import httpx
		from cdp_use import CDPClient

		# If the cdp_url is already a websocket URL, use it as-is.
		if self.cdp_url.startswith('ws'):
			ws_url = self.cdp_url
		else:
			# Otherwise, treat it as the DevTools HTTP root and fetch the websocket URL.
			url = self.cdp_url.rstrip('/')
			if not url.endswith('/json/version'):
				url = url + '/json/version'
			async with httpx.AsyncClient() as client:
				version_info = await client.get(url)
				ws_url = version_info.json()['webSocketDebuggerUrl']

		cdp_client = CDPClient(ws_url)
		await cdp_client.start()
		return cdp_client

	async def create_cdp_session_for_target(self, target_id: str) -> Any:
		"""Create a new CDP session attached to a specific target/frame.

		Args:
			target_id: The target ID to attach to

		Returns:
			CDPClient with session attached to target - caller is responsible for cleanup

		Note: The returned CDPClient should be stopped when done using:
			await cdp_client.stop()
		"""
		cdp_client = await self._create_fresh_cdp_client()

		try:
			# Attach to the target
			session = await cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
			session_id = session['sessionId']

			# Store the session_id on the client for convenience
			cdp_client.target_session_id = session_id

			# Enable necessary domains
			await cdp_client.send.Target.setAutoAttach(
				params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}, session_id=session_id
			)

			await asyncio.gather(
				cdp_client.send.DOM.enable(session_id=session_id),
				cdp_client.send.Accessibility.enable(session_id=session_id),
				cdp_client.send.DOMSnapshot.enable(session_id=session_id),
				cdp_client.send.Page.enable(session_id=session_id),
			)

			return cdp_client

		except Exception:
			# Clean up on error
			await cdp_client.stop()
			raise

	async def create_cdp_session_for_frame(self, frame_id: str) -> Any:
		"""Create a new CDP session for a specific frame by finding its parent target.

		Args:
			frame_id: The frame ID to find and attach to

		Returns:
			CDPClient with session attached to the target containing this frame

		Raises:
			ValueError: If frame_id is not found in any target
		"""
		search_client = await self._create_fresh_cdp_client()

		try:
			# Get all targets
			targets = await search_client.send.Target.getTargets()

			# Search through page targets to find which one contains the frame
			for target in targets['targetInfos']:
				if target['type'] != 'page':
					continue

				# Attach to this target to check its frame tree
				session = await search_client.send.Target.attachToTarget(params={'targetId': target['targetId'], 'flatten': True})
				temp_session_id = session['sessionId']

				# Enable Page domain to get frame tree
				await search_client.send.Page.enable(session_id=temp_session_id)

				# Get frame tree for this target
				frame_tree = await search_client.send.Page.getFrameTree(session_id=temp_session_id)

				# Recursively search for the frame_id
				def search_frame_tree(node) -> bool:
					if node['frame']['id'] == frame_id:
						return True
					if 'childFrames' in node:
						for child in node['childFrames']:
							if search_frame_tree(child):
								return True
					return False

				if search_frame_tree(frame_tree['frameTree']):
					# Found the target containing this frame
					await search_client.stop()

					# Create a new session for this target
					return await self.create_cdp_session_for_target(target['targetId'])

			# Frame not found
			await search_client.stop()
			raise ValueError(f'Frame with ID {frame_id} not found in any target')

		except Exception:
			await search_client.stop()
			raise

	async def create_cdp_session_for_node(self, node: Any) -> Any:
		"""Create a new CDP session for a specific DOM node's target.

		Args:
			node: The EnhancedDOMTreeNode to create a session for

		Returns:
			CDPClient with session attached to the node's target

		Raises:
			ValueError: If node doesn't have a target_id or node doesn't exist in target
		"""
		if not hasattr(node, 'target_id') or not node.target_id:
			raise ValueError(f'Node does not have a target_id: {node}')

		# Create session for the node's target
		cdp_client = await self.create_cdp_session_for_target(node.target_id)

		try:
			# Verify the node exists in this target
			# Use the stored session_id from the client
			session_id = getattr(cdp_client, 'target_session_id', None)
			if not session_id:
				raise ValueError('CDP client does not have target_session_id set')

			result = await cdp_client.send.DOM.describeNode(params={'backendNodeId': node.backend_node_id}, session_id=session_id)

			# If we get here without exception, the node exists
			return cdp_client

		except Exception as e:
			# Node doesn't exist in this target, clean up
			await cdp_client.stop()
			raise ValueError(f'Node with backend_node_id {node.backend_node_id} not found in target {node.target_id}: {e}')

	async def get_current_target_info(self) -> dict | None:
		"""Get info about the current active target using CDP."""
		if not self.current_target_id:
			return None

		targets = await self.cdp_client.send.Target.getTargets()
		for target in targets.get('targetInfos', []):
			if target.get('targetId') == self.current_target_id:
				return target
		return None

	async def get_current_page_url(self) -> str:
		"""Get the URL of the current page using CDP."""
		target = await self.get_current_target_info()
		if target:
			return target.get('url', '')
		return ''

	async def get_current_page_title(self) -> str:
		"""Get the title of the current page using CDP."""
		if not self.current_target_id:
			return ''

		try:
			session = await self.cdp_client.send.Target.attachToTarget(
				params={'targetId': self.current_target_id, 'flatten': True}
			)
			session_id = session['sessionId']
			title_result = await self.cdp_client.send.Runtime.evaluate(
				params={'expression': 'document.title'}, session_id=session_id
			)
			title = title_result.get('result', {}).get('value', '')
			await self.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})
			return title
		except Exception:
			return ''

	# ========== DOM Helper Methods ==========

	def update_cached_selector_map(self, selector_map: dict[int, 'EnhancedDOMTreeNode']) -> None:
		"""Update the cached selector map with new DOM state.

		This should be called by the DOM watchdog after rebuilding the DOM.

		Args:
			selector_map: The new selector map from DOM serialization
		"""
		self._cached_selector_map = selector_map

	async def get_dom_element_by_index(self, index: int) -> 'EnhancedDOMTreeNode | None':
		"""Get DOM element by index.

		First checks cached selector map, then falls back to DOM watchdog
		which may trigger a DOM rebuild if needed.

		Args:
			index: The element index from the serialized DOM

		Returns:
			EnhancedDOMTreeNode or None if index not found
		"""
		# First check cached selector map
		if self._cached_selector_map and index in self._cached_selector_map:
			return self._cached_selector_map[index]

		# Fall back to DOM watchdog which may rebuild DOM
		if self._dom_watchdog:
			node = await self._dom_watchdog.get_element_by_index(index)
			# Update cache if watchdog rebuilt the DOM
			if self._dom_watchdog.selector_map:
				self._cached_selector_map = self._dom_watchdog.selector_map
			return node

		return None

	# Alias for backwards compatibility
	async def get_element_by_index(self, index: int) -> 'EnhancedDOMTreeNode | None':
		"""Alias for get_dom_element_by_index for backwards compatibility."""
		return await self.get_dom_element_by_index(index)

	def is_file_input(self, element: Any) -> bool:
		"""Check if element is a file input.

		Args:
			element: The DOM element to check

		Returns:
			True if element is a file input, False otherwise
		"""
		if self._dom_watchdog:
			return self._dom_watchdog.is_file_input(element)
		# Fallback if watchdog not available
		return (
			hasattr(element, 'node_name')
			and element.node_name.upper() == 'INPUT'
			and hasattr(element, 'attributes')
			and element.attributes.get('type', '').lower() == 'file'
		)

	def clear_dom_cache(self) -> None:
		"""Clear cached DOM state to force rebuild on next access."""
		if self._dom_watchdog:
			self._dom_watchdog.clear_cache()

	async def get_selector_map(self) -> dict[int, 'EnhancedDOMTreeNode']:
		"""Get the current selector map from cached state or DOM watchdog.

		Returns:
			Dictionary mapping element indices to EnhancedDOMTreeNode objects
		"""
		# First try cached selector map
		if self._cached_selector_map:
			return self._cached_selector_map

		# Try to get from DOM watchdog
		if self._dom_watchdog and hasattr(self._dom_watchdog, 'selector_map'):
			return self._dom_watchdog.selector_map or {}

		# Return empty dict if nothing available
		return {}

	async def remove_highlights(self) -> None:
		"""Remove highlights from the page using CDP."""
		if self.cdp_client and self.current_target_id:
			try:
				# Attach to current target
				session = await self.cdp_client.send.Target.attachToTarget(
					params={'targetId': self.current_target_id, 'flatten': True}
				)
				session_id = session['sessionId']

				# Remove highlights via JavaScript - matches the highlighting system from debug/highlights.py
				script = """
					// Remove all browser-use highlight elements
					const highlights = document.querySelectorAll('[data-browser-use-highlight]');
					highlights.forEach(el => el.remove());
				"""
				await self.cdp_client.send.Runtime.evaluate(params={'expression': script}, session_id=session_id)

				# Detach from target
				await self.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})
			except Exception as e:
				self.logger.debug(f'Failed to remove highlights: {e}')

	@property
	def downloaded_files(self) -> list[str]:
		"""Get list of downloaded files from the downloads directory."""
		if not self.browser_profile.downloads_path:
			return []

		downloads_dir = Path(self.browser_profile.downloads_path)
		if not downloads_dir.exists():
			return []

		# Get all files in downloads directory (not directories)
		files = [str(f) for f in downloads_dir.iterdir() if f.is_file()]
		return sorted(files)

	# ========== CDP-based replacements for browser_context operations ==========

	async def _cdp_execute_on_target(
		self, target_id: str, commands: list[tuple[str, dict]] | None = None, callable_fn: Any | None = None
	) -> Any:
		"""Execute CDP commands on a specific target with automatic attach/detach.

		Args:
			target_id: The target ID to attach to
			commands: List of (method, params) tuples to execute, e.g. [('Runtime.evaluate', {'expression': '...'})]
			callable_fn: Alternative - async function that receives (cdp_client, session_id) and returns result

		Returns:
			Result of the last command or callable_fn return value
		"""
		# Attach to target
		session = await self.cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
		session_id = session['sessionId']

		try:
			if callable_fn:
				# Execute the provided async function
				return await callable_fn(self.cdp_client, session_id)
			elif commands:
				# Execute the list of commands
				result = None
				for method, params in commands:
					# Parse the method name to get domain and command
					domain, command = method.split('.')
					# Get the domain object dynamically
					domain_obj = getattr(self.cdp_client.send, domain)
					# Call the command with params and session_id
					# CDP library expects params as first arg (can be None), session_id as second
					cmd_func = getattr(domain_obj, command)
					if params:
						result = await cmd_func(params=params, session_id=session_id)
					else:
						result = await cmd_func(session_id=session_id)
				return result
			else:
				return session_id  # Just return session_id if no commands
		finally:
			# Always detach from target
			await self.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})

	async def _cdp_get_all_pages(self) -> list[dict]:
		"""Get all browser pages/tabs using CDP Target.getTargets."""
		targets = await self.cdp_client.send.Target.getTargets()
		# Filter for page targets only
		return [t for t in targets.get('targetInfos', []) if t.get('type') == 'page']

	async def _cdp_create_new_page(self, url: str = 'about:blank') -> str:
		"""Create a new page/tab using CDP Target.createTarget. Returns target ID."""
		result = await self.cdp_client.send.Target.createTarget(params={'url': url, 'newWindow': False, 'background': False})
		return result['targetId']

	async def _cdp_close_page(self, target_id: str) -> None:
		"""Close a page/tab using CDP Target.closeTarget."""
		await self.cdp_client.send.Target.closeTarget(params={'targetId': target_id})

	async def _cdp_activate_page(self, target_id: str) -> None:
		"""Activate/focus a page using CDP Target.activateTarget."""
		await self.cdp_client.send.Target.activateTarget(params={'targetId': target_id})

	async def _cdp_get_cookies(self, urls: list[str] | None = None) -> list[dict]:
		"""Get cookies using CDP Network.getCookies."""
		params = {'urls': urls} if urls else {}
		result = await self.cdp_client.send.Network.getCookies(**params)
		return result.get('cookies', [])

	async def _cdp_set_cookies(self, cookies: list[dict]) -> None:
		"""Set cookies using CDP Network.setCookies."""
		await self.cdp_client.send.Network.setCookies(params={'cookies': cookies})

	async def _cdp_clear_cookies(self) -> None:
		"""Clear all cookies using CDP Network.clearBrowserCookies."""
		await self.cdp_client.send.Network.clearBrowserCookies()

	async def _cdp_set_extra_headers(self, headers: dict[str, str]) -> None:
		"""Set extra HTTP headers using CDP Network.setExtraHTTPHeaders."""
		await self.cdp_client.send.Network.setExtraHTTPHeaders(params={'headers': headers})

	async def _cdp_grant_permissions(self, permissions: list[str], origin: str | None = None) -> None:
		"""Grant permissions using CDP Browser.grantPermissions."""
		params = {'permissions': permissions}
		if origin:
			params['origin'] = origin
		await self.cdp_client.send.Browser.grantPermissions(**params)

	async def _cdp_set_geolocation(self, latitude: float, longitude: float, accuracy: float = 100) -> None:
		"""Set geolocation using CDP Emulation.setGeolocationOverride."""
		await self.cdp_client.send.Emulation.setGeolocationOverride(
			params={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy}
		)

	async def _cdp_clear_geolocation(self) -> None:
		"""Clear geolocation override using CDP."""
		await self.cdp_client.send.Emulation.clearGeolocationOverride()

	async def _cdp_add_init_script(self, script: str) -> str:
		"""Add script to evaluate on new document using CDP Page.addScriptToEvaluateOnNewDocument."""
		result = await self.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(params={'source': script})
		return result['identifier']

	async def _cdp_remove_init_script(self, identifier: str) -> None:
		"""Remove script added with addScriptToEvaluateOnNewDocument."""
		await self.cdp_client.send.Page.removeScriptToEvaluateOnNewDocument(params={'identifier': identifier})

	async def _cdp_set_viewport(self, width: int, height: int, device_scale_factor: float = 1.0, mobile: bool = False) -> None:
		"""Set viewport using CDP Emulation.setDeviceMetricsOverride."""
		await self.cdp_client.send.Emulation.setDeviceMetricsOverride(
			params={'width': width, 'height': height, 'deviceScaleFactor': device_scale_factor, 'mobile': mobile}
		)

	async def _cdp_get_storage_state(self) -> dict:
		"""Get storage state (cookies, localStorage, sessionStorage) using CDP."""
		# Get cookies
		cookies_result = await self.cdp_client.send.Network.getCookies()
		cookies = cookies_result.get('cookies', [])

		# Get localStorage and sessionStorage would require evaluating JavaScript
		# on each origin, which is more complex. For now, return cookies only.
		return {
			'cookies': cookies,
			'origins': [],  # Would need to iterate through origins for localStorage/sessionStorage
		}

	async def _cdp_navigate(self, url: str, target_id: str | None = None) -> None:
		"""Navigate to URL using CDP Page.navigate."""
		# Use provided target_id or fall back to current_target_id
		target_to_use = target_id or self.current_target_id

		if not target_to_use:
			# If no target available, get the first page target
			targets = await self._cdp_get_all_pages()
			if targets:
				target_to_use = targets[0]['targetId']
				self.current_target_id = target_to_use
			else:
				raise ValueError('No target available for navigation')

		# Use helper to navigate on the target
		await self._cdp_execute_on_target(target_to_use, commands=[('Page.enable', {}), ('Page.navigate', {'url': url})])

	async def cdp_client_for_frame(self, frame_id: str) -> Any:
		"""Get a CDP client attached to the target containing the specified frame.

		Iterates through all targets, checks their frame trees to find which target
		contains the frame with the given frame_id, then returns a CDP client
		attached to that target.

		Args:
			frame_id: The frame ID to search for

		Returns:
			CDP client attached to the target containing the frame

		Raises:
			ValueError: If the frame is not found in any target
		"""
		# Get all targets
		targets = await self.cdp_client.send.Target.getTargets()
		all_targets = targets.get('targetInfos', [])

		# Check each target's frame tree
		for target in all_targets:
			target_id = target.get('targetId')
			if not target_id:
				continue

			# Attach to target to get frame tree
			session = await self.cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
			session_id = session['sessionId']

			try:
				# Enable Page domain first
				await self.cdp_client.send.Page.enable(session_id=session_id)
				
				# Get the frame tree for this target
				frame_tree_result = await self.cdp_client.send.Page.getFrameTree(session_id=session_id)

				# Check if frame_id exists in this target's frame tree
				def frame_exists_in_tree(frame_node: dict) -> bool:
					"""Recursively check if frame_id exists in the frame tree."""
					# Check current frame
					if frame_node.get('frame', {}).get('id') == frame_id:
						return True

					# Check child frames recursively
					child_frames = frame_node.get('childFrames', [])
					for child in child_frames:
						if frame_exists_in_tree(child):
							return True

					return False

				# Check if the frame exists in this target's tree
				if frame_exists_in_tree(frame_tree_result.get('frameTree', {})):
					# Frame found! Keep the session attached and return a client for it
					# Note: We're returning the cdp_client with the session already attached
					# The caller is responsible for detaching when done
					return self.cdp_client, session_id, target_id

			finally:
				# Detach from target if frame not found
				await self.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})

		# Frame not found in any target
		raise ValueError(f"Frame with ID '{frame_id}' not found in any target")

	async def cdp_client_for_target(self, target_id: str) -> Any:
		"""Get a CDP client attached to a specific target.

		This is a simpler helper that just attaches to a specific target ID.

		Args:
			target_id: The target ID to attach to

		Returns:
			Tuple of (cdp_client, session_id) for the attached target
		"""
		session = await self.cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
		session_id = session['sessionId']

		# Return the client and session_id
		# Caller is responsible for detaching when done
		return self.cdp_client, session_id


# Import uuid7str for ID generation
try:
	from uuid_extensions import uuid7str
except ImportError:
	import uuid

	def uuid7str() -> str:
		return str(uuid.uuid4())


# Fix Pydantic circular dependency for all watchdogs
# This must be called after BrowserSession class is fully defined
_watchdog_modules = [
	'browser_use.browser.crash_watchdog.CrashWatchdog',
	'browser_use.browser.downloads_watchdog.DownloadsWatchdog',
	'browser_use.browser.local_browser_watchdog.LocalBrowserWatchdog',
	'browser_use.browser.storage_state_watchdog.StorageStateWatchdog',
	'browser_use.browser.navigation_watchdog.NavigationWatchdog',
	'browser_use.browser.aboutblank_watchdog.AboutBlankWatchdog',
	'browser_use.browser.default_action_watchdog.DefaultActionWatchdog',
	'browser_use.browser.dom_watchdog.DOMWatchdog',
]

for module_path in _watchdog_modules:
	try:
		module_name, class_name = module_path.rsplit('.', 1)
		module = __import__(module_name, fromlist=[class_name])
		watchdog_class = getattr(module, class_name)
		watchdog_class.model_rebuild()
	except Exception:
		pass  # Ignore if watchdog can't be imported or rebuilt
