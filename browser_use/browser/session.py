"""Event-driven browser session with backwards compatibility."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Self

import httpx
from bubus import EventBus
from cdp_use import CDPClient
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str

from browser_use.browser.events import (
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserLaunchEvent,
	BrowserStartEvent,
	BrowserStateRequestEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.views import BrowserStateSummary, TabInfo
from browser_use.dom.views import EnhancedDOMTreeNode, TargetInfo
from browser_use.utils import _log_pretty_url, is_new_tab_page

DEFAULT_BROWSER_PROFILE = BrowserProfile()

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


class CachedSession:
	"""Container for cached CDP session to allow weak references."""

	def __init__(self, client: Any, session_id: str, target_id: str, frame_id: str | None = None):
		self.client = client
		self.session_id = session_id
		self.target_id = target_id
		self.frame_id = frame_id

	def __hash__(self):
		return hash(self.client)


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
		revalidate_instances='never',  # resets private attrs on every model rebuild
	)

	# Core configuration
	id: str = Field(default_factory=lambda: str(uuid7str()))

	cdp_url: str | None = None
	is_local: bool = Field(default=True)
	cdp_session_cache_enabled: bool = Field(
		default=True,
		description='Cache CDP sessions based on target_id, set False to create a fresh CDP session for every single CDP call',
	)
	browser_profile: BrowserProfile = Field(
		default_factory=lambda: DEFAULT_BROWSER_PROFILE,
		description='BrowserProfile() options to use for the session, otherwise a default profile will be used',
	)

	# Main shared event bus for all browser session + all watchdogs
	event_bus: EventBus = Field(default_factory=EventBus)

	# Mutable public state
	current_target_id: str | None = None

	# Mutable private state shared between watchdogs
	_cdp_client_root: CDPClient | None = PrivateAttr(default=None)
	_cached_browser_state_summary: Any = PrivateAttr(default=None)
	_cached_selector_map: dict[int, 'EnhancedDOMTreeNode'] = PrivateAttr(default_factory=dict)

	# Watchdogs
	_crash_watchdog: Any | None = PrivateAttr(default=None)
	_downloads_watchdog: Any | None = PrivateAttr(default=None)
	_aboutblank_watchdog: Any | None = PrivateAttr(default=None)
	_navigation_watchdog: Any | None = PrivateAttr(default=None)
	_storage_state_watchdog: Any | None = PrivateAttr(default=None)
	_local_browser_watchdog: Any | None = PrivateAttr(default=None)
	_default_action_watchdog: Any | None = PrivateAttr(default=None)
	_dom_watchdog: Any | None = PrivateAttr(default=None)
	_screenshot_watchdog: Any | None = PrivateAttr(default=None)

	_logger: Any = PrivateAttr(default=None)

	@property
	def logger(self) -> Any:
		"""Get instance-specific logger with session ID in the name"""
		# if self._logger is None or not self._cdp_client_root:  # keep updating the name pre-init because our id and str(self) can change until we are connected
		# 	self._logger = logging.getLogger(f'browser_use.{self}')
		return logging.getLogger(f'browser_use.{self}')

	def __repr__(self) -> str:
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0]
		return f'BrowserSession🆂 {self.id[-4:]}:{port_number} #{str(id(self))[-2:]} (cdp_url={self.cdp_url}, profile={self.browser_profile})'

	def __str__(self) -> str:
		# Note: _original_browser_session tracking moved to Agent class
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0]
		return (
			f'BrowserSession🆂 {self.id[-4:]}:{port_number} #{str(id(self))[-2:]}'  # ' 🅟 {str(id(self.current_target_id))[-2:]}'
		)

	async def reset(self) -> None:
		"""Clear all cached CDP sessions with proper cleanup."""

		# TODO: clear the event bus queue here, implement this helper
		# await self.event_bus.wait_for_idle(timeout=5.0)
		# await self.event_bus.clear()


		self._cdp_client_root = None  # type: ignore
		self._cached_browser_state_summary = None
		self._cached_selector_map.clear()

		self.current_target_id = None
		if self.is_local:
			self.cdp_url = None

		self._crash_watchdog = None
		self._downloads_watchdog = None
		self._aboutblank_watchdog = None
		self._navigation_watchdog = None
		self._storage_state_watchdog = None
		self._local_browser_watchdog = None
		self._default_action_watchdog = None
		self._dom_watchdog = None
		self._screenshot_watchdog = None

	@property
	def cdp_client(self) -> CDPClient:
		"""Get the cached root CDP client. The client is created and started in self.connect()."""
		assert self._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
		return self._cdp_client_root

	def model_post_init(self, __context) -> None:
		"""Register event handlers after model initialization."""
		# Register BrowserSession's event handlers manually since it's not a BaseWatchdog
		if not getattr(self.event_bus, '_attached_root_listeners', None):
			self.event_bus.on(BrowserStartEvent, self.on_BrowserStartEvent)
			self.event_bus.on(BrowserStopEvent, self.on_BrowserStopEvent)
			self.event_bus._attached_root_listeners = True  # type: ignore

	async def start(self) -> None:
		"""Start the browser session."""
		await self.event_bus.dispatch(BrowserStartEvent())

	async def on_BrowserStartEvent(self, event: BrowserStartEvent) -> dict[str, str]:
		"""Handle browser start request.

		Returns:
			Dict with 'cdp_url' key containing the CDP URL
		"""

		# await self.reset()

		# Initialize and attach all watchdogs FIRST so LocalBrowserWatchdog can handle BrowserLaunchEvent
		await self.attach_all_watchdogs()

		try:
			# If no CDP URL, launch local browser
			if not self.cdp_url:
				if self.is_local:
					# Launch local browser using event-driven approach
					launch_event = self.event_bus.dispatch(BrowserLaunchEvent())
					await launch_event

					# Get the CDP URL from LocalBrowserWatchdog handler result
					results = await launch_event.event_results_flat_dict(
						raise_if_none=True, raise_if_any=True, raise_if_conflicts=True
					)
					self.cdp_url = results.get('cdp_url')

					if not self.cdp_url:
						raise RuntimeError('LocalBrowserWatchdog failed to return a CDP URL for the launched browser')
				else:
					raise ValueError('Got BrowserSession(is_local=False) but no cdp_url was provided to connect to!')

			assert self.cdp_url and '://' in self.cdp_url

			# Setup browser via CDP (for both local and remote cases)
			await self.connect(cdp_url=self.cdp_url)
			assert self.cdp_client is not None

			# Notify that browser is connected (single place)
			self.event_bus.dispatch(BrowserConnectedEvent(cdp_url=self.cdp_url))

			# Return the CDP URL for other components
			return {'cdp_url': self.cdp_url}

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
			# Check if we should keep the browser alive
			if self.browser_profile.keep_alive and not event.force:
				self.event_bus.dispatch(BrowserStoppedEvent(reason='Kept alive due to keep_alive=True'))
				return

			# Clear CDP session cache before stopping
			await self.reset()

			# Reset state
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

	async def get_cdp_session(self, target_id: str | None = None) -> tuple[CDPClient, str]:
		"""Get or create a CDP session for a target, using cache when enabled.

		Args:
			target_id: The target ID to get a session for

		Returns:
			Tuple of (cdp_client, session_id)
		"""
		assert self.cdp_url is not None, 'CDP URL not set - browser may not be configured or launched yet'
		assert self._cdp_client_root is not None, 'Root CDP client not initialized - browser may not be connected yet'
		assert self.cdp_session_cache_enabled, (
			'cdp_session_cache_enabled should be always enabled to force us to make sure it works properly'
		)

		if not target_id:
			target_id = self.current_target_id
			if not target_id:
				raise ValueError('No target ID provided and no current target ID set')

		await self._cdp_client_root.send.Target.setAutoAttach(
			params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
		)

		session = await self._cdp_client_root.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
		session_id = session['sessionId']
		await self._cdp_enable_all_domains(self._cdp_client_root, session_id)
		return self._cdp_client_root, session_id


	async def _cdp_enable_all_domains(self, client: Any, session_id: str) -> None:
		"""Enable all necessary CDP domains for a session."""
		# Enable auto-attach for related targets (iframes, etc)
		await client.send.Target.setAutoAttach(
			params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}, session_id=session_id
		)

		# Enable all commonly used domains in parallel
		await asyncio.gather(
			client.send.Page.enable(session_id=session_id),
			client.send.DOM.enable(session_id=session_id),
			client.send.DOMSnapshot.enable(session_id=session_id),
			client.send.Accessibility.enable(session_id=session_id),
			client.send.Runtime.enable(session_id=session_id),
			client.send.Inspector.enable(session_id=session_id),
			# TEMPORARILY DISABLED: Network.enable causes excessive event logging
			# client.send.Network.enable(session_id=session_id),
			return_exceptions=True,  # Don't fail if some domains aren't available
		)

	# ========== Helper Methods ==========

	async def get_browser_state_summary(
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

		# Dispatch the event and wait for result
		event = self.event_bus.dispatch(
			BrowserStateRequestEvent(
				include_dom=True,
				include_screenshot=include_screenshot,
				cache_clickable_elements_hashes=cache_clickable_elements_hashes,
			)
		)

		# The handler returns the BrowserStateSummary directly
		result = await event.event_result(raise_if_none=True, raise_if_any=True)
		assert isinstance(result, BrowserStateSummary)
		return result

	async def get_state_summary(self, cache_clickable_elements_hashes: bool = True) -> 'BrowserStateSummary':
		"""Alias for get_browser_state_with_recovery for backwards compatibility."""
		return await self.get_browser_state_summary(
			cache_clickable_elements_hashes=cache_clickable_elements_hashes, include_screenshot=False
		)

	async def attach_all_watchdogs(self) -> None:
		"""Initialize and attach all watchdogs with explicit handler registration."""
		from browser_use.browser.aboutblank_watchdog import AboutBlankWatchdog
		from browser_use.browser.crash_watchdog import CrashWatchdog
		from browser_use.browser.default_action_watchdog import DefaultActionWatchdog
		from browser_use.browser.dom_watchdog import DOMWatchdog
		from browser_use.browser.downloads_watchdog import DownloadsWatchdog
		from browser_use.browser.local_browser_watchdog import LocalBrowserWatchdog
		from browser_use.browser.navigation_watchdog import NavigationWatchdog
		from browser_use.browser.screenshot_watchdog import ScreenshotWatchdog
		from browser_use.browser.storage_state_watchdog import StorageStateWatchdog

		# Initialize CrashWatchdog
		CrashWatchdog.model_rebuild()
		self._crash_watchdog = CrashWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserConnectedEvent, self._crash_watchdog.on_BrowserConnectedEvent)
		# self.event_bus.on(BrowserStoppedEvent, self._crash_watchdog.on_BrowserStoppedEvent)
		await self._crash_watchdog.attach_to_session()

		# Initialize DownloadsWatchdog
		DownloadsWatchdog.model_rebuild()
		self._downloads_watchdog = DownloadsWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserLaunchEvent, self._downloads_watchdog.on_BrowserLaunchEvent)
		# self.event_bus.on(TabCreatedEvent, self._downloads_watchdog.on_TabCreatedEvent)
		# self.event_bus.on(TabClosedEvent, self._downloads_watchdog.on_TabClosedEvent)
		# self.event_bus.on(BrowserStoppedEvent, self._downloads_watchdog.on_BrowserStoppedEvent)
		# self.event_bus.on(NavigationCompleteEvent, self._downloads_watchdog.on_NavigationCompleteEvent)
		await self._downloads_watchdog.attach_to_session()

		# Initialize StorageStateWatchdog
		StorageStateWatchdog.model_rebuild()
		self._storage_state_watchdog = StorageStateWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserConnectedEvent, self._storage_state_watchdog.on_BrowserConnectedEvent)
		# self.event_bus.on(BrowserStopEvent, self._storage_state_watchdog.on_BrowserStopEvent)
		# self.event_bus.on(SaveStorageStateEvent, self._storage_state_watchdog.on_SaveStorageStateEvent)
		# self.event_bus.on(LoadStorageStateEvent, self._storage_state_watchdog.on_LoadStorageStateEvent)
		await self._storage_state_watchdog.attach_to_session()

		# Initialize LocalBrowserWatchdog
		LocalBrowserWatchdog.model_rebuild()
		self._local_browser_watchdog = LocalBrowserWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserLaunchEvent, self._local_browser_watchdog.on_BrowserLaunchEvent)
		# self.event_bus.on(BrowserKillEvent, self._local_browser_watchdog.on_BrowserKillEvent)
		# self.event_bus.on(BrowserStopEvent, self._local_browser_watchdog.on_BrowserStopEvent)
		await self._local_browser_watchdog.attach_to_session()

		# Initialize NavigationWatchdog
		NavigationWatchdog.model_rebuild()
		self._navigation_watchdog = NavigationWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserConnectedEvent, self._navigation_watchdog.on_BrowserConnectedEvent)
		# self.event_bus.on(BrowserStoppedEvent, self._navigation_watchdog.on_BrowserStoppedEvent)
		# self.event_bus.on(TabCreatedEvent, self._navigation_watchdog.on_TabCreatedEvent)
		# self.event_bus.on(TabClosedEvent, self._navigation_watchdog.on_TabClosedEvent)
		# self.event_bus.on(SwitchTabEvent, self._navigation_watchdog.on_SwitchTabEvent)
		# self.event_bus.on(NavigateToUrlEvent, self._navigation_watchdog.on_NavigateToUrlEvent)
		# self.event_bus.on(NavigationCompleteEvent, self._navigation_watchdog.on_NavigationCompleteEvent)
		await self._navigation_watchdog.attach_to_session()

		# Initialize AboutBlankWatchdog
		AboutBlankWatchdog.model_rebuild()
		self._aboutblank_watchdog = AboutBlankWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserStopEvent, self._aboutblank_watchdog.on_BrowserStopEvent)
		# self.event_bus.on(BrowserStoppedEvent, self._aboutblank_watchdog.on_BrowserStoppedEvent)
		# self.event_bus.on(TabCreatedEvent, self._aboutblank_watchdog.on_TabCreatedEvent)
		# self.event_bus.on(TabClosedEvent, self._aboutblank_watchdog.on_TabClosedEvent)
		await self._aboutblank_watchdog.attach_to_session()

		# Initialize DefaultActionWatchdog
		DefaultActionWatchdog.model_rebuild()
		self._default_action_watchdog = DefaultActionWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(ClickElementEvent, self._default_action_watchdog.on_ClickElementEvent)
		# self.event_bus.on(TypeTextEvent, self._default_action_watchdog.on_TypeTextEvent)
		# self.event_bus.on(ScrollEvent, self._default_action_watchdog.on_ScrollEvent)
		# self.event_bus.on(GoBackEvent, self._default_action_watchdog.on_GoBackEvent)
		# self.event_bus.on(GoForwardEvent, self._default_action_watchdog.on_GoForwardEvent)
		# self.event_bus.on(RefreshEvent, self._default_action_watchdog.on_RefreshEvent)
		# self.event_bus.on(WaitEvent, self._default_action_watchdog.on_WaitEvent)
		# self.event_bus.on(SendKeysEvent, self._default_action_watchdog.on_SendKeysEvent)
		# self.event_bus.on(UploadFileEvent, self._default_action_watchdog.on_UploadFileEvent)
		# self.event_bus.on(ScrollToTextEvent, self._default_action_watchdog.on_ScrollToTextEvent)
		await self._default_action_watchdog.attach_to_session()

		# Initialize DOMWatchdog
		DOMWatchdog.model_rebuild()
		self._dom_watchdog = DOMWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(TabCreatedEvent, self._dom_watchdog.on_TabCreatedEvent)
		# self.event_bus.on(BrowserStateRequestEvent, self._dom_watchdog.on_BrowserStateRequestEvent)
		await self._dom_watchdog.attach_to_session()

		# Initialize ScreenshotWatchdog
		ScreenshotWatchdog.model_rebuild()
		self._screenshot_watchdog = ScreenshotWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserStartEvent, self._screenshot_watchdog.on_BrowserStartEvent)
		# self.event_bus.on(BrowserStoppedEvent, self._screenshot_watchdog.on_BrowserStoppedEvent)
		# self.event_bus.on(ScreenshotEvent, self._screenshot_watchdog.on_ScreenshotEvent)
		await self._screenshot_watchdog.attach_to_session()

	async def connect(self, cdp_url: str | None = None) -> Self:
		"""Connect to a remote chromium-based browser via CDP using cdp-use.

		This MUST succeed or the browser is unusable. Fails hard on any error.
		"""

		self.cdp_url = cdp_url or self.cdp_url
		if not self.cdp_url:
			raise RuntimeError('Cannot setup CDP connection without CDP URL')

		if not self.cdp_url.startswith('ws'):
			# If it's an HTTP URL, fetch the WebSocket URL from /json/version endpoint
			url = self.cdp_url.rstrip('/')
			if not url.endswith('/json/version'):
				url = url + '/json/version'
			async with httpx.AsyncClient() as client:
				version_info = await client.get(url)
				self.cdp_url = version_info.json()['webSocketDebuggerUrl']

		assert self.cdp_url is not None

		browser_location = 'local browser' if self.is_local else 'remote browser'
		self.logger.info(f'🌎 Connecting to existing chromium-based browser via CDP: {self.cdp_url} -> ({browser_location})')

		try:
			# Import cdp-use client

			# Convert HTTP URL to WebSocket URL if needed

			# Create and store the CDP client for direct CDP communication
			self._cdp_client_root = CDPClient(self.cdp_url)
			assert self._cdp_client_root is not None
			await self._cdp_client_root.start()
			self.logger.info('✅ CDP client connected successfully')

			# Get browser targets to find available contexts/pages
			targets = await self._cdp_client_root.send.Target.getTargets()

			# Find main browser pages (avoiding iframes, workers, extensions, etc.)
			page_targets: list[TargetInfo] = [
				t
				for t in targets['targetInfos']
				if self._is_valid_target(
					t, include_http=True, include_about=True, include_pages=True, include_iframes=False, include_workers=False
				)
			]

			# Check for chrome://newtab pages and immediately redirect them
			# to about:blank to avoid JS issues from CDP on chrome://* urls
			from browser_use.utils import is_new_tab_page

			for target in page_targets:
				target_url = target.get('url', '')
				if is_new_tab_page(target_url) and target_url != 'about:blank':
					# Redirect chrome://newtab to about:blank to avoid JS issues preventing driving chrome://newtab
					target_id = target['targetId']
					self.logger.info(f'🔄 Redirecting {target_url} to about:blank for target {target_id}')
					try:
						# Use cached session to navigate to about:blank
						client, session_id = await self.get_cdp_session(target_id)
						await client.send.Page.navigate(params={'url': 'about:blank'}, session_id=session_id)
					except Exception as e:
						self.logger.warning(f'Failed to redirect {target_url} to about:blank: {e}')

			if not page_targets:
				# No pages found, create a new one
				new_target = await self._cdp_client_root.send.Target.createTarget(params={'url': 'about:blank'})
				target_id = new_target['targetId']
				self.logger.info(f'📄 Created new blank page with target ID: {target_id}')
			else:
				# Use the first available page
				target_id = page_targets[0]['targetId']
				self.logger.info(f'📄 Using existing page with target ID: {target_id}')

			# Store the current page target ID
			self.current_target_id = target_id

			# Pre-create cached session for the current target (enables all domains)
			try:
				await self.get_cdp_session(target_id)
				assert self.cdp_client is not None
			except Exception as e:
				self.logger.warning(f'Failed to create CDP session: {e}')
				raise

		except Exception as e:
			# Fatal error - browser is not usable without CDP connection
			self.logger.error(f'❌ FATAL: Failed to setup CDP connection: {e}')
			self.logger.error('❌ Browser cannot continue without CDP connection')
			# Clean up any partial state
			self._cdp_client_root = None
			self.current_target_id = None
			# Re-raise as a fatal error
			raise RuntimeError(f'Failed to establish CDP connection to browser: {e}') from e

		return self

	async def get_target_id_by_tab_index(self, tab_index: int) -> str | None:
		"""Get target ID by tab index."""
		target_ids = await self._cdp_get_all_pages()
		if 0 <= tab_index < len(target_ids):
			return target_ids[tab_index]['targetId']
		return None

	async def get_tab_index(self, target_id: str) -> int:
		"""Get tab index for a target ID."""
		target_ids = await self._cdp_get_all_pages()
		if target_id in target_ids:
			return target_ids.index(target_id)
		return -1

	async def get_tabs(self) -> list[TabInfo]:
		"""Get information about all open tabs using CDP Target.getTargetInfo for speed."""
		tabs = []

		# Get all page targets using CDP
		pages = await self._cdp_get_all_pages()

		for i, page_target in enumerate(pages):
			target_id = page_target['targetId']
			url = page_target['url']

			# Try to get the title directly from Target.getTargetInfo - much faster!
			# The initial getTargets() doesn't include title, but getTargetInfo does
			try:
				target_info = await self.cdp_client.send.Target.getTargetInfo(params={'targetId': target_id})
				# The title is directly available in targetInfo
				title = target_info.get('targetInfo', {}).get('title', '')

				# Skip JS execution for chrome:// pages and new tab pages
				if is_new_tab_page(url) or url.startswith('chrome://'):
					# Use URL as title for chrome pages, or mark new tabs as unusable
					if is_new_tab_page(url):
						title = 'ignore this tab and do not use it'
					elif not title:
						# For chrome:// pages without a title, use the URL itself
						title = url

				# Special handling for PDF pages without titles
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
				# Fallback to basic title handling
				self.logger.debug(f'⚠️ Failed to get target info for tab #{i}: {_log_pretty_url(url)} - {type(e).__name__}')

				if is_new_tab_page(url):
					title = 'ignore this tab and do not use it'
				elif url.startswith('chrome://'):
					title = url
				else:
					title = ''

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

	# ========== CDP Helper Methods ==========

	async def cdp_clients_for_target(self, target_id: str) -> list['CDPClient']:
		"""Get CDP clients for a target, including main and iframe sessions.

		Returns list with root target session first, then iframe sessions.
		"""
		if not self._cdp_client_root:
			raise ValueError('CDP client not initialized')

		clients = []

		# Get cached session for main target
		client, session_id = await self.get_cdp_session(target_id)

		# For now, return just the main client with session
		# In future, we'd enumerate iframes and attach to them too
		clients.append(client)

		return clients

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
			# Skip invalid targets
			if not self._is_valid_target(
				target, include_http=True, include_about=True, include_pages=True, include_iframes=True, include_workers=False
			):
				continue

			# Check if this target contains the frame
			frames = await self.frames_by_target(target['targetId'])
			if frame_id in frames:
				return target['targetId']

		return None

	async def get_current_page_cdp_session_id(self) -> str | None:
		"""Get the CDP session ID for the current page."""
		if not hasattr(self, 'current_target_id') or not self.current_target_id:
			return None

		# Get cached session ID
		client, session_id = await self.get_cdp_session(self.current_target_id)
		return session_id

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
			Tuple of (CDPClient, session_id) - uses cached session when available
		"""
		# Just use the cached session
		return await self.get_cdp_session(target_id)

	async def create_cdp_session_for_frame(self, frame_id: str) -> Any:
		"""Create a new CDP session for a specific frame by finding its parent target.

		Args:
			frame_id: The frame ID to find and attach to

		Returns:
			Tuple of (CDPClient, session_id) for the target containing this frame

		Raises:
			ValueError: If frame_id is not found in any target
		"""
		# Get all targets using main client
		targets = await self.cdp_client.send.Target.getTargets()

		# Search through page targets to find which one contains the frame
		for target in targets['targetInfos']:
			# Skip invalid targets
			if not self._is_valid_target(target):
				continue

			if target['type'] != 'page':
				continue

			# Use cached session to check frame tree
			client, temp_session_id = await self.get_cdp_session(target['targetId'])

			# Get frame tree for this target
			frame_tree = await client.send.Page.getFrameTree(session_id=temp_session_id)

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
				# Found the target containing this frame - return cached session
				return await self.get_cdp_session(target['targetId'])

		# Frame not found
		raise ValueError(f'Frame with ID {frame_id} not found in any target')

	async def create_cdp_session_for_node(self, node: Any) -> Any:
		"""Create a new CDP session for a specific DOM node's target.

		Args:
			node: The EnhancedDOMTreeNode to create a session for

		Returns:
			Tuple of (CDPClient, session_id) for the node's target

		Raises:
			ValueError: If node doesn't have a target_id or node doesn't exist in target
		"""
		if not hasattr(node, 'target_id') or not node.target_id:
			raise ValueError(f'Node does not have a target_id: {node}')

		# Get cached session for the node's target
		client, session_id = await self.get_cdp_session(node.target_id)

		# Verify the node exists in this target
		try:
			await client.send.DOM.describeNode(params={'backendNodeId': node.backend_node_id}, session_id=session_id)
			# If we get here without exception, the node exists
			return client, session_id
		except Exception as e:
			raise ValueError(f'Node with backend_node_id {node.backend_node_id} not found in target {node.target_id}: {e}')

	async def get_current_target_info(self) -> dict | None:
		"""Get info about the current active target using CDP."""
		if not self.current_target_id:
			return None

		targets = await self.cdp_client.send.Target.getTargets()
		for target in targets.get('targetInfos', []):
			if target.get('targetId') == self.current_target_id:
				# Still return even if it's not a "valid" target since we're looking for a specific ID
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
		try:
			if not self.current_target_id:
				return

			# Get cached session
			client, session_id = await self.get_cdp_session(self.current_target_id)

			# Remove highlights via JavaScript
			script = """
					// Remove all browser-use highlight elements
					const highlights = document.querySelectorAll('[data-browser-use-highlight]');
					highlights.forEach(el => el.remove());
			"""
			await client.send.Runtime.evaluate(params={'expression': script}, session_id=session_id)
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
		"""Execute CDP commands on a specific target using cached session.

		Args:
			target_id: The target ID to execute commands on
			commands: List of (method, params) tuples to execute, e.g. [('Runtime.evaluate', {'expression': '...'})]
			callable_fn: Alternative - async function that receives (cdp_client, session_id) and returns result

		Returns:
			Result of the last command or callable_fn return value
		"""
		# Get cached session or create new one
		client, session_id = await self.get_cdp_session(target_id)

		if callable_fn:
			return await callable_fn(client, session_id)
		elif commands:
			result = None
			for method, params in commands:
				domain, command = method.split('.')
				domain_obj = getattr(client.send, domain)
				cmd_func = getattr(domain_obj, command)
				result = await cmd_func(params=params, session_id=session_id) if params else await cmd_func(session_id=session_id)
			return result
		else:
			return session_id

	async def _cdp_get_all_pages(
		self,
		include_http: bool = True,
		include_about: bool = True,
		include_pages: bool = True,
		include_iframes: bool = False,
		include_workers: bool = False,
		include_chrome: bool = False,
		include_chrome_extensions: bool = False,
		include_chrome_error: bool = False,
	) -> list[TargetInfo]:
		"""Get all browser pages/tabs using CDP Target.getTargets."""
		targets = await self.cdp_client.send.Target.getTargets()
		# Filter for valid page/tab targets only
		return [
			t
			for t in targets.get('targetInfos', [])
			if self._is_valid_target(
				t,
				include_http=include_http,
				include_about=include_about,
				include_pages=include_pages,
				include_iframes=include_iframes,
				include_workers=include_workers,
				include_chrome=include_chrome,
				include_chrome_extensions=include_chrome_extensions,
				include_chrome_error=include_chrome_error,
			)
		]

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
		if not self.current_target_id:
			return []

		client, session_id = await self.get_cdp_session(self.current_target_id)
		params = {'urls': urls} if urls else {}
		result = await client.send.Network.getCookies(params=params, session_id=session_id)
		return result.get('cookies', [])

	async def _cdp_set_cookies(self, cookies: list[dict]) -> None:
		"""Set cookies using CDP Network.setCookies."""
		if not self.current_target_id or not cookies:
			return

		client, session_id = await self.get_cdp_session(self.current_target_id)
		await client.send.Network.setCookies(params={'cookies': cookies}, session_id=session_id)

	async def _cdp_clear_cookies(self) -> None:
		"""Clear all cookies using CDP Network.clearBrowserCookies."""
		if not self.current_target_id:
			return

		client, session_id = await self.get_cdp_session(self.current_target_id)
		await client.send.Network.clearBrowserCookies(session_id=session_id)

	async def _cdp_set_extra_headers(self, headers: dict[str, str]) -> None:
		"""Set extra HTTP headers using CDP Network.setExtraHTTPHeaders."""
		if not self.current_target_id:
			return

		client, session_id = await self.get_cdp_session(self.current_target_id)
		await client.send.Network.setExtraHTTPHeaders(params={'headers': headers}, session_id=session_id)

	async def _cdp_grant_permissions(self, permissions: list[str], origin: str | None = None) -> None:
		"""Grant permissions using CDP Browser.grantPermissions."""
		params = {'permissions': permissions}
		if origin:
			params['origin'] = origin
		client, session_id = await self.get_cdp_session()
		await client.send.Browser.grantPermissions(params=params, session_id=session_id)

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
		assert self._cdp_client_root is not None
		client, session_id = await self.get_cdp_session()

		result = await client.send.Page.addScriptToEvaluateOnNewDocument(
			params={'source': script, 'runImmediately': True}
		)
		return result['identifier']

	async def _cdp_remove_init_script(self, identifier: str) -> None:
		"""Remove script added with addScriptToEvaluateOnNewDocument."""
		client, session_id = await self.get_cdp_session(target_id='main')
		await client.send.Page.removeScriptToEvaluateOnNewDocument(params={'identifier': identifier}, session_id=session_id)

	async def _cdp_set_viewport(self, width: int, height: int, device_scale_factor: float = 1.0, mobile: bool = False) -> None:
		"""Set viewport using CDP Emulation.setDeviceMetricsOverride."""
		await self.cdp_client.send.Emulation.setDeviceMetricsOverride(
			params={'width': width, 'height': height, 'deviceScaleFactor': device_scale_factor, 'mobile': mobile}
		)

	async def _cdp_get_storage_state(self) -> dict:
		"""Get storage state (cookies, localStorage, sessionStorage) using CDP."""
		# Use the _cdp_get_cookies helper which handles session attachment
		cookies = await self._cdp_get_cookies()

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

	@staticmethod
	def _is_valid_target(
		target_info: TargetInfo,
		include_http: bool = True,
		include_chrome: bool = False,
		include_chrome_extensions: bool = False,
		include_chrome_error: bool = False,
		include_about: bool = True,
		include_iframes: bool = True,
		include_pages: bool = True,
		include_workers: bool = False,
	) -> bool:
		"""Check if a target should be processed.

		Args:
			target_info: Target info dict from CDP

		Returns:
			True if target should be processed, False if it should be skipped
		"""
		target_type = target_info.get('type', '')
		url = target_info.get('url', '')

		url_allowed, type_allowed = False, False

		if url.startswith('chrome-error://') and include_chrome_error:
			url_allowed = True

		if url.startswith('chrome://') and include_chrome:
			url_allowed = True

		if url.startswith('chrome-extension://') and include_chrome_extensions:
			url_allowed = True

		# dont allow about:srcdoc! there are also other rare about: pages that we want to avoid
		if url == 'about:blank' and include_about:
			url_allowed = True

		if (url.startswith('http://') or url.startswith('https://')) and include_http:
			url_allowed = True

		if target_type in ('service_worker', 'shared_worker', 'worker') and include_workers:
			type_allowed = True

		if target_type in ('page', 'tab') and include_pages:
			type_allowed = True

		if target_type in ('iframe', 'webview') and include_iframes:
			type_allowed = True

		return url_allowed and type_allowed

	async def get_all_frames(self) -> tuple[dict[str, dict], dict[str, str]]:
		"""Get a complete frame hierarchy from all browser targets.

		Returns:
			Tuple of (all_frames, target_sessions) where:
			- all_frames: dict mapping frame_id -> frame info dict with all metadata
			- target_sessions: dict mapping target_id -> session_id for active sessions
		"""
		all_frames = {}  # frame_id -> FrameInfo dict
		target_sessions = {}  # target_id -> session_id (keep sessions alive during collection)

		# Get all targets
		targets = await self._cdp_get_all_pages(
			include_http=True,
			include_about=True,
			include_pages=True,
			include_iframes=True,
			include_workers=False,
			include_chrome=False,
			include_chrome_extensions=False,
			include_chrome_error=True,
		)
		all_targets = targets

		# First pass: collect frame trees from ALL targets
		for target in all_targets:
			target_id = target['targetId']

			# Get cached session for this target
			client, session_id = await self.get_cdp_session(target_id)
			target_sessions[target_id] = session_id

			try:
				# Try to get frame tree (not all target types support this)
				try:
					frame_tree_result = await client.send.Page.getFrameTree(session_id=session_id)

					# Process the frame tree recursively
					def process_frame_tree(node, parent_frame_id=None):
						"""Recursively process frame tree and add to all_frames."""
						frame = node.get('frame', {})
						current_frame_id = frame.get('id')

						if current_frame_id:
							# For iframe targets, check if the frame has a parentId field
							# This indicates it's an OOPIF with a parent in another target
							actual_parent_id = frame.get('parentId') or parent_frame_id

							# Create frame info with all CDP response data plus our additions
							frame_info = {
								**frame,  # Include all original frame data: id, url, parentId, etc.
								'frameTargetId': target_id,  # Target that can access this frame
								'parentFrameId': actual_parent_id,  # Use parentId from frame if available
								'childFrameIds': [],  # Will be populated below
								'isCrossOrigin': False,  # Will be determined based on context
								'isValidTarget': self._is_valid_target(
									target,
									include_http=True,
									include_about=True,
									include_pages=True,
									include_iframes=True,
									include_workers=False,
									include_chrome=False,  # chrome://newtab, chrome://settings, etc. are not valid frames we can control (for sanity reasons)
									include_chrome_extensions=False,  # chrome-extension://
									include_chrome_error=False,  # chrome-error://  (e.g. when iframes fail to load or are blocked by uBlock Origin)
								),
							}

							# Check if frame is cross-origin based on crossOriginIsolatedContextType
							cross_origin_type = frame.get('crossOriginIsolatedContextType')
							if cross_origin_type and cross_origin_type != 'NotIsolated':
								frame_info['isCrossOrigin'] = True

							# For iframe targets, the frame itself is likely cross-origin
							if target.get('type') == 'iframe':
								frame_info['isCrossOrigin'] = True

							# Add child frame IDs (note: OOPIFs won't appear here)
							child_frames = node.get('childFrames', [])
							for child in child_frames:
								child_frame = child.get('frame', {})
								child_frame_id = child_frame.get('id')
								if child_frame_id:
									frame_info['childFrameIds'].append(child_frame_id)

							# Store or merge frame info
							if current_frame_id in all_frames:
								# Frame already seen from another target, merge info
								existing = all_frames[current_frame_id]
								# If this is an iframe target, it has direct access to the frame
								if target.get('type') == 'iframe':
									existing['frameTargetId'] = target_id
									existing['isCrossOrigin'] = True
							else:
								all_frames[current_frame_id] = frame_info

							# Process child frames recursively
							for child in child_frames:
								process_frame_tree(child, current_frame_id)

					# Process the entire frame tree
					process_frame_tree(frame_tree_result.get('frameTree', {}))

				except Exception:
					# Target doesn't support Page domain or has no frames
					pass

			except Exception:
				# Error processing this target
				pass

		# Second pass: populate backend node IDs and parent target IDs
		await self._populate_frame_metadata(all_frames, target_sessions)

		return all_frames, target_sessions

	async def _populate_frame_metadata(self, all_frames: dict[str, dict], target_sessions: dict[str, str]) -> None:
		"""Populate additional frame metadata like backend node IDs and parent target IDs.

		Args:
			all_frames: Frame hierarchy dict to populate
			target_sessions: Active target sessions
		"""
		for frame_id_iter, frame_info in all_frames.items():
			parent_frame_id = frame_info.get('parentFrameId')

			if parent_frame_id and parent_frame_id in all_frames:
				parent_frame_info = all_frames[parent_frame_id]
				parent_target_id = parent_frame_info.get('frameTargetId')

				# Store parent target ID
				frame_info['parentTargetId'] = parent_target_id

				# Try to get backend node ID from parent context
				if parent_target_id in target_sessions:
					assert parent_target_id is not None
					parent_session_id = target_sessions[parent_target_id]
					try:
						# Enable DOM domain
						await self.cdp_client.send.DOM.enable(session_id=parent_session_id)

						# Get frame owner info to find backend node ID
						frame_owner = await self.cdp_client.send.DOM.getFrameOwner(
							params={'frameId': frame_id_iter}, session_id=parent_session_id
						)

						if frame_owner:
							frame_info['backendNodeId'] = frame_owner.get('backendNodeId')
							frame_info['nodeId'] = frame_owner.get('nodeId')

					except Exception:
						# Frame owner not available (likely cross-origin)
						pass

	async def find_frame_target(self, frame_id: str, all_frames: dict[str, dict] | None = None) -> dict | None:
		"""Find the frame info for a specific frame ID.

		Args:
			frame_id: The frame ID to search for
			all_frames: Optional pre-built frame hierarchy. If None, will call get_all_frames()

		Returns:
			Frame info dict if found, None otherwise
		"""
		if all_frames is None:
			all_frames, _ = await self.get_all_frames()

		return all_frames.get(frame_id)

	async def cdp_client_for_frame(self, frame_id: str) -> Any:
		"""Get a CDP client attached to the target containing the specified frame.

		Builds a unified frame hierarchy from all targets to find the correct target
		for any frame, including OOPIFs (Out-of-Process iframes).

		Args:
			frame_id: The frame ID to search for

		Returns:
			Tuple of (cdp_client, session_id, target_id) for the target containing the frame

		Raises:
			ValueError: If the frame is not found in any target
		"""
		# Get complete frame hierarchy
		all_frames, target_sessions = await self.get_all_frames()

		# Find the requested frame
		frame_info = await self.find_frame_target(frame_id, all_frames)

		if frame_info:
			target_id = frame_info.get('frameTargetId')

			if target_id in target_sessions:
				assert target_id is not None
				# Use existing session
				session_id = target_sessions[target_id]
				# Return the client with session attached
				return self.cdp_client, session_id, target_id

		# Frame not found
		raise ValueError(f"Frame with ID '{frame_id}' not found in any target")

	async def cdp_client_for_target(self, target_id: str) -> Any:
		"""Get a CDP client attached to a specific target.

		This is a simpler helper that just gets a cached session for a target.

		Args:
			target_id: The target ID to attach to

		Returns:
			Tuple of (cdp_client, session_id) for the target
		"""
		return await self.get_cdp_session(target_id)


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
	'browser_use.browser.screenshot_watchdog.ScreenshotWatchdog',
]

for module_path in _watchdog_modules:
	try:
		module_name, class_name = module_path.rsplit('.', 1)
		module = __import__(module_name, fromlist=[class_name])
		watchdog_class = getattr(module, class_name)
		watchdog_class.model_rebuild()
	except Exception:
		pass  # Ignore if watchdog can't be imported or rebuilt
