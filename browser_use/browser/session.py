"""Event-driven browser session with backwards compatibility."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Self, cast

import httpx
from bubus import EventBus
from cdp_use import CDPClient
from cdp_use.cdp.network import Cookie
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str

# Silence cdp_use logging - it's too verbose at DEBUG level
# We need to patch the logger in the cdp_use.client module directly
import cdp_use.client
cdp_use.client.logger.setLevel(logging.ERROR)

# Also configure all cdp_use loggers
for logger_name in ['cdp_use', 'cdp_use.client', 'cdp_use.cdp', 'cdp_use.cdp.registry']:
	cdp_logger = logging.getLogger(logger_name)
	cdp_logger.setLevel(logging.ERROR)
	cdp_logger.propagate = False  # Don't propagate to root logger

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


class CDPSession(BaseModel):
	"""Info about a single CDP session bound to a specific target."""

	model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')

	cdp_client: CDPClient

	target_id: str
	session_id: str
	title: str = 'Unknown title'
	url: str = 'about:blank'

	@classmethod
	async def for_target(cls, cdp_client: CDPClient, target_id: str):
		cdp_session = cls(
			cdp_client=cdp_client,
			target_id=target_id,
			session_id='connecting',
		)
		return await cdp_session.attach()

	async def attach(self) -> Self:
		result = await self.cdp_client.send.Target.attachToTarget(
			params={
				'targetId': self.target_id,
				'flatten': True,
				'filter': [  # type: ignore
					{'type': 'page', 'exclude': False},
					{'type': 'iframe', 'exclude': False},
				],
			}
		)
		self.session_id = result['sessionId']

		# Enable all domains in parallel
		await asyncio.gather(
			self.cdp_client.send.Page.enable(session_id=self.session_id),
			self.cdp_client.send.DOM.enable(session_id=self.session_id),
			self.cdp_client.send.DOMSnapshot.enable(session_id=self.session_id),
			self.cdp_client.send.Accessibility.enable(session_id=self.session_id),
			self.cdp_client.send.Runtime.enable(session_id=self.session_id),
			self.cdp_client.send.Inspector.enable(session_id=self.session_id),
			# TEMPORARILY DISABLED: Network.enable causes excessive event logging
			# self.cdp_client.send.Network.enable(session_id=self.session_id),
			return_exceptions=True,  # Don't fail if some domains aren't available
		)
		target_info = await self.get_target_info()
		self.title = target_info['title']
		self.url = target_info['url']
		return self

	async def get_tab_info(self) -> TabInfo:
		target_info = await self.get_target_info()
		return TabInfo(
			page_id=-1,  # TODO: get tab order efficiently somehow
			url=target_info['url'],
			title=target_info['title'],
		)

	async def get_target_info(self) -> TargetInfo:
		result = await self.cdp_client.send.Target.getTargetInfo(params={'targetId': self.target_id})
		return result['targetInfo']


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
	browser_profile: BrowserProfile = Field(
		default_factory=lambda: DEFAULT_BROWSER_PROFILE,
		description='BrowserProfile() options to use for the session, otherwise a default profile will be used',
	)

	# Main shared event bus for all browser session + all watchdogs
	event_bus: EventBus = Field(default_factory=EventBus)

	# Mutable public state
	agent_focus: CDPSession | None = None

	# Mutable private state shared between watchdogs
	_cdp_client_root: CDPClient | None = PrivateAttr(default=None)
	_cdp_session_pool: dict[str, CDPSession] = PrivateAttr(default_factory=dict)
	_cached_browser_state_summary: Any = PrivateAttr(default=None)
	_cached_selector_map: dict[int, EnhancedDOMTreeNode] = PrivateAttr(default_factory=dict)

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
		# **regenerate it every time** because our id and str(self) can change as browser connection state changes
		# if self._logger is None or not self._cdp_client_root:
		# 	self._logger = logging.getLogger(f'browser_use.{self}')
		return logging.getLogger(f'browser_use.{self}')

	def __repr__(self) -> str:
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0]
		return f'BrowserSession🆂 {self.id[-4:]}:{port_number} #{str(id(self))[-2:]} (cdp_url={self.cdp_url}, profile={self.browser_profile})'

	def __str__(self) -> str:
		# Note: _original_browser_session tracking moved to Agent class
		port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0]
		return f'BrowserSession🆂 {self.id[-4:]}:{port_number} #{str(id(self))[-2:]}'  # ' 🅟 {str(id(self.cdp_session.target_id))[-2:]}'

	async def reset(self) -> None:
		"""Clear all cached CDP sessions with proper cleanup."""

		# TODO: clear the event bus queue here, implement this helper
		# await self.event_bus.wait_for_idle(timeout=5.0)
		# await self.event_bus.clear()

		self._cdp_client_root = None  # type: ignore
		self._cached_browser_state_summary = None
		self._cached_selector_map.clear()

		self.agent_focus = None
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

	def model_post_init(self, __context) -> None:
		"""Register event handlers after model initialization."""
		# Check if handlers are already registered to prevent duplicates
		start_handlers = self.event_bus.handlers.get('BrowserStartEvent', [])
		start_handler_names = [getattr(h, '__name__', str(h)) for h in start_handlers]
		
		if any('on_BrowserStartEvent' in name for name in start_handler_names):
			raise RuntimeError(
				f'[BrowserSession] Duplicate handler registration attempted! '
				f'on_BrowserStartEvent is already registered. '
				f'This likely means BrowserSession was initialized multiple times with the same EventBus.'
			)
			
		# Register BrowserSession's event handlers
		self.event_bus.on(BrowserStartEvent, self.on_BrowserStartEvent)
		self.event_bus.on(BrowserStopEvent, self.on_BrowserStopEvent)

	async def start(self) -> None:
		"""Start the browser session."""
		await self.event_bus.dispatch(BrowserStartEvent())

	async def kill(self) -> None:
		"""Kill the browser session."""
		await self.event_bus.dispatch(BrowserStopEvent(force=True))
		await self.event_bus.stop(clear=True, timeout=5)
		self.event_bus = EventBus()

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

	@property
	def cdp_client(self) -> CDPClient:
		"""Get the cached root CDP cdp_session.cdp_client. The client is created and started in self.connect()."""
		assert self._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
		return self._cdp_client_root

	async def get_or_create_cdp_session(self, target_id: str | None = None, focus: bool = True) -> CDPSession:
		"""Get or create a CDP session for a target.
		
		Args:
			target_id: Target ID to get session for. If None, uses current agent focus.
			focus: If True, switches agent focus to this target. If False, just returns session without changing focus.
		
		Returns:
			CDPSession for the specified target.
		"""
		assert self.cdp_url is not None, 'CDP URL not set - browser may not be configured or launched yet'
		assert self._cdp_client_root is not None, 'Root CDP client not initialized - browser may not be connected yet'
		assert self.agent_focus is not None, 'CDP session not initialized - browser may not be connected yet'

		# If no target_id specified, use the current target_id
		if target_id is None:
			target_id = self.agent_focus.target_id
		
		# Check if we already have a session for this target in the pool
		if target_id in self._cdp_session_pool:
			session = self._cdp_session_pool[target_id]
			if focus and self.agent_focus.target_id != target_id:
				self.logger.debug(f'[get_or_create_cdp_session] Switching agent focus from {self.agent_focus.target_id} to {target_id}')
				self.agent_focus = session
			else:
				self.logger.debug(f'[get_or_create_cdp_session] Reusing existing session for {target_id} (focus={focus})')
			return session
		
		# If it's the current focus target, return that session
		if self.agent_focus.target_id == target_id:
			self._cdp_session_pool[target_id] = self.agent_focus
			return self.agent_focus

		# Create new session for this target
		self.logger.debug(f'[get_or_create_cdp_session] Creating new CDP session for target {target_id}')
		session = await CDPSession.for_target(self._cdp_client_root, target_id)
		self._cdp_session_pool[target_id] = session
		
		# Only change agent focus if requested
		if focus:
			self.logger.debug(f'[get_or_create_cdp_session] Switching agent focus from {self.agent_focus.target_id} to {target_id}')
			self.agent_focus = session
		else:
			self.logger.debug(f'[get_or_create_cdp_session] Created session for {target_id} without changing focus (still on {self.agent_focus.target_id})')
		
		return session

	@property
	def current_target_id(self) -> str | None:
		return self.agent_focus.target_id if self.agent_focus else None

	@property
	def current_session_id(self) -> str | None:
		return self.agent_focus.session_id if self.agent_focus else None

	# ========== Helper Methods ==========

	async def get_browser_state_summary(
		self, cache_clickable_elements_hashes: bool = True, include_screenshot: bool = True, cached: bool = False
	) -> BrowserStateSummary:
		if cached and self._cached_browser_state_summary is not None and self._cached_browser_state_summary.dom_state:
			# Don't use cached state if it has 0 interactive elements
			selector_map = self._cached_browser_state_summary.dom_state.selector_map
			
			# Don't use cached state if we need a screenshot but the cached state doesn't have one
			if include_screenshot and not self._cached_browser_state_summary.screenshot:
				self.logger.debug('⚠️ Cached browser state has no screenshot, fetching fresh state with screenshot')
			elif selector_map and len(selector_map) > 0:
				self.logger.debug('🔄 Using pre-cached browser state summary for open tab')
				return self._cached_browser_state_summary
			else:
				self.logger.debug('⚠️ Cached browser state has 0 interactive elements, fetching fresh state')

		# Dispatch the event and wait for result
		event: BrowserStateRequestEvent = cast(
			BrowserStateRequestEvent,
			self.event_bus.dispatch(
				BrowserStateRequestEvent(
					include_dom=True,
					include_screenshot=include_screenshot,
					cache_clickable_elements_hashes=cache_clickable_elements_hashes,
				)
			),
		)

		# The handler returns the BrowserStateSummary directly
		result = await event.event_result(raise_if_none=True, raise_if_any=True)
		assert result is not None
		return result

	async def attach_all_watchdogs(self) -> None:
		"""Initialize and attach all watchdogs with explicit handler registration."""
		# Prevent duplicate watchdog attachment
		if hasattr(self, '_watchdogs_attached') and self._watchdogs_attached:
			self.logger.debug('Watchdogs already attached, skipping duplicate attachment')
			return
		
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

		ScreenshotWatchdog.model_rebuild()
		self._screenshot_watchdog = ScreenshotWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(BrowserStartEvent, self._screenshot_watchdog.on_BrowserStartEvent)
		# self.event_bus.on(BrowserStoppedEvent, self._screenshot_watchdog.on_BrowserStoppedEvent)
		# self.event_bus.on(ScreenshotEvent, self._screenshot_watchdog.on_ScreenshotEvent)
		await self._screenshot_watchdog.attach_to_session()

		# Initialize DOMWatchdog (depends on ScreenshotWatchdog being registered)
		DOMWatchdog.model_rebuild()
		self._dom_watchdog = DOMWatchdog(event_bus=self.event_bus, browser_session=self)
		# self.event_bus.on(TabCreatedEvent, self._dom_watchdog.on_TabCreatedEvent)
		# self.event_bus.on(BrowserStateRequestEvent, self._dom_watchdog.on_BrowserStateRequestEvent)
		await self._dom_watchdog.attach_to_session()
		
		# Mark watchdogs as attached to prevent duplicate attachment
		self._watchdogs_attached = True

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
			await self._cdp_client_root.send.Target.setAutoAttach(
				params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
			)
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

			# Collect all targets that need redirection
			redirected_targets = []
			for target in page_targets:
				target_url = target.get('url', '')
				if is_new_tab_page(target_url) and target_url != 'about:blank':
					# Redirect chrome://newtab to about:blank to avoid JS issues preventing driving chrome://newtab
					target_id = target['targetId']
					self.logger.info(f'🔄 Redirecting {target_url} to about:blank for target {target_id}')
					try:
						# Create a CDP session for this target
						temp_session = await CDPSession.for_target(self._cdp_client_root, target_id)
						# Navigate to about:blank
						await temp_session.cdp_client.send.Page.navigate(
							params={'url': 'about:blank'}, session_id=temp_session.session_id
						)
						redirected_targets.append(target_id)
						# Update the target's URL in our list for later use
						target['url'] = 'about:blank'
						# Small delay to ensure navigation completes
						await asyncio.sleep(0.1)
					except Exception as e:
						self.logger.warning(f'Failed to redirect {target_url} to about:blank: {e}')
			
			# Log summary of redirections
			if redirected_targets:
				self.logger.info(f'✅ Redirected {len(redirected_targets)} chrome://newtab pages to about:blank')

			if not page_targets:
				# No pages found, create a new one
				new_target = await self._cdp_client_root.send.Target.createTarget(params={'url': 'about:blank'})
				target_id = new_target['targetId']
				self.logger.info(f'📄 Created new blank page with target ID: {target_id}')
			else:
				# Use the first available page
				target_id = page_targets[0]['targetId']
				self.logger.info(f'📄 Using existing page with target ID: {target_id}')

			# Store the current page target ID and add to pool
			self.agent_focus = await CDPSession.for_target(self._cdp_client_root, target_id)
			self._cdp_session_pool[target_id] = self.agent_focus

			# Verify the session is working
			try:
				assert self.agent_focus.title != 'Unknown title'
			except Exception as e:
				self.logger.warning(f'Failed to create CDP session: {e}')
				raise

		except Exception as e:
			# Fatal error - browser is not usable without CDP connection
			self.logger.error(f'❌ FATAL: Failed to setup CDP connection: {e}')
			self.logger.error('❌ Browser cannot continue without CDP connection')
			# Clean up any partial state
			self._cdp_client_root = None
			self.agent_focus = None
			# Re-raise as a fatal error
			raise RuntimeError(f'Failed to establish CDP connection to browser: {e}') from e

		return self

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

	# ========== ID Lookup Methods ==========

	async def get_tab_index(self, target_id: str) -> int:
		"""Get tab index for a target ID."""
		targets = await self._cdp_get_all_pages()
		target_ids = [t['targetId'] for t in targets]
		if target_id in target_ids:
			return target_ids.index(target_id)
		return -1

	async def get_target_id_by_tab_index(self, tab_index: int) -> str | None:
		"""Get target ID by tab index."""
		target_ids = await self._cdp_get_all_pages()
		if 0 <= tab_index < len(target_ids):
			return target_ids[tab_index]['targetId']
		return None

	async def get_current_target_info(self) -> TargetInfo | None:
		"""Get info about the current active target using CDP."""
		if not self.agent_focus or not self.agent_focus.target_id:
			return None

		targets = await self.cdp_client.send.Target.getTargets()
		for target in targets.get('targetInfos', []):
			if target.get('targetId') == self.agent_focus.target_id:
				# Still return even if it's not a "valid" target since we're looking for a specific ID
				return target
		return None

	async def get_current_page_url(self) -> str:
		"""Get the URL of the current page using CDP."""
		target = await self.get_current_target_info()
		if target:
			return target.get('url', '')
		return 'about:blank'

	async def get_current_page_title(self) -> str:
		"""Get the title of the current page using CDP."""
		target_info = await self.get_current_target_info()
		if target_info:
			return target_info.get('title', 'Unknown page title')
		return 'Unknown page title'

	# ========== DOM Helper Methods ==========

	async def get_dom_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
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

	def update_cached_selector_map(self, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
		"""Update the cached selector map with new DOM state.

		This should be called by the DOM watchdog after rebuilding the DOM.

		Args:
			selector_map: The new selector map from DOM serialization
		"""
		self._cached_selector_map = selector_map

	# Alias for backwards compatibility
	async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
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

	async def get_selector_map(self) -> dict[int, EnhancedDOMTreeNode]:
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
			if not self.agent_focus or not self.agent_focus.target_id:
				return

			# Get cached session
			cdp_session = await self.get_or_create_cdp_session()

			# Remove highlights via JavaScript
			script = """
					// Remove all browser-use highlight elements
					const highlights = document.querySelectorAll('[data-browser-use-highlight]');
					highlights.forEach(el => el.remove());
			"""
			await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': script}, session_id=cdp_session.session_id)
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

	async def _cdp_get_cookies(self) -> list[Cookie]:
		"""Get cookies using CDP Network.getCookies."""
		cdp_session = await self.get_or_create_cdp_session()
		result = await cdp_session.cdp_client.send.Storage.getCookies(session_id=cdp_session.session_id)
		return result.get('cookies', [])

	async def _cdp_set_cookies(self, cookies: list[Cookie]) -> None:
		"""Set cookies using CDP Storage.setCookies."""
		if not self.agent_focus or not cookies:
			return

		cdp_session = await self.get_or_create_cdp_session()
		# Storage.setCookies expects params dict with 'cookies' key
		await cdp_session.cdp_client.send.Storage.setCookies(
			params={'cookies': cookies},  # type: ignore[arg-type]
			session_id=cdp_session.session_id,
		)

	async def _cdp_clear_cookies(self) -> None:
		"""Clear all cookies using CDP Network.clearBrowserCookies."""
		cdp_session = await self.get_or_create_cdp_session()
		await cdp_session.cdp_client.send.Storage.clearCookies(session_id=cdp_session.session_id)

	async def _cdp_set_extra_headers(self, headers: dict[str, str]) -> None:
		"""Set extra HTTP headers using CDP Network.setExtraHTTPHeaders."""
		if not self.agent_focus:
			return

		cdp_session = await self.get_or_create_cdp_session()
		# await cdp_session.cdp_client.send.Network.setExtraHTTPHeaders(params={'headers': headers}, session_id=cdp_session.session_id)
		raise NotImplementedError('Not implemented yet')

	async def _cdp_grant_permissions(self, permissions: list[str], origin: str | None = None) -> None:
		"""Grant permissions using CDP Browser.grantPermissions."""
		params = {'permissions': permissions}
		# if origin:
		# 	params['origin'] = origin
		cdp_session = await self.get_or_create_cdp_session()
		# await cdp_session.cdp_client.send.Browser.grantPermissions(params=params, session_id=cdp_session.session_id)
		raise NotImplementedError('Not implemented yet')

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
		cdp_session = await self.get_or_create_cdp_session()

		result = await cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
			params={'source': script, 'runImmediately': True}
		)
		return result['identifier']

	async def _cdp_remove_init_script(self, identifier: str) -> None:
		"""Remove script added with addScriptToEvaluateOnNewDocument."""
		cdp_session = await self.get_or_create_cdp_session(target_id='main')
		await cdp_session.cdp_client.send.Page.removeScriptToEvaluateOnNewDocument(
			params={'identifier': identifier}, session_id=cdp_session.session_id
		)

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

		assert self._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
		assert self.agent_focus is not None, 'CDP session not initialized - browser may not be connected yet'

		self.agent_focus = await CDPSession.for_target(self._cdp_client_root, target_id or self.agent_focus.target_id)

		# Use helper to navigate on the target
		await self.agent_focus.cdp_client.send.Page.navigate(params={'url': url}, session_id=self.agent_focus.session_id)

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

			# Get cached session for this target (don't change focus - iterating frames)
			cdp_session = await self.get_or_create_cdp_session(target_id, focus=False)
			target_sessions[target_id] = cdp_session.session_id

			try:
				# Try to get frame tree (not all target types support this)
				try:
					frame_tree_result = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)

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

	async def cdp_client_for_target(self, target_id: str) -> CDPSession:
		return await self.get_or_create_cdp_session(target_id, focus=False)

	async def cdp_client_for_frame(self, frame_id: str) -> CDPSession:
		"""Get a CDP client attached to the target containing the specified frame.

		Builds a unified frame hierarchy from all targets to find the correct target
		for any frame, including OOPIFs (Out-of-Process iframes).

		Args:
			frame_id: The frame ID to search for

		Returns:
			Tuple of (cdp_cdp_session, target_id) for the target containing the frame

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
				# Return the client with session attached (don't change focus)
				return await self.get_or_create_cdp_session(target_id, focus=False)

		# Frame not found
		raise ValueError(f"Frame with ID '{frame_id}' not found in any target")

	async def cdp_client_for_node(self, node: EnhancedDOMTreeNode) -> CDPSession:
		"""Get CDP client for a specific DOM node based on its frame."""
		if node.frame_id:
			return await self.cdp_client_for_frame(node.frame_id)
		return await self.get_or_create_cdp_session()


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
