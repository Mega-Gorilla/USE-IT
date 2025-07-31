"""Browser watchdog for monitoring crashes and network timeouts."""

import asyncio
import time
from typing import Any, Dict, Set
from weakref import WeakSet

from bubus import EventBus
from playwright.async_api import Browser, BrowserContext, Page, Request, Response
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStartedEvent,
	BrowserStoppedEvent,
	PageCrashedEvent,
	TabCreatedEvent,
	TabClosedEvent,
)
from browser_use.utils import logger


class NetworkRequestTracker:
	"""Tracks ongoing network requests."""
	
	def __init__(self, request: Request, start_time: float, url: str, method: str, resource_type: str | None = None):
		self.request = request
		self.start_time = start_time
		self.url = url
		self.method = method
		self.resource_type = resource_type


class CrashWatchdog(BaseModel):
	"""Monitors browser health for crashes and network timeouts."""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	event_bus: EventBus
	network_timeout_seconds: float = Field(default=10.0)
	check_interval_seconds: float = Field(default=1.0)

	# Private state
	_browser: Browser | None = PrivateAttr(default=None)
	_browser_context: BrowserContext | None = PrivateAttr(default=None)
	_browser_pid: int | None = PrivateAttr(default=None)
	_pages: WeakSet[Page] = PrivateAttr(default_factory=WeakSet)
	_active_requests: Dict[str, NetworkRequestTracker] = PrivateAttr(default_factory=dict)
	_monitoring_task: asyncio.Task | None = PrivateAttr(default=None)
	_cdp_session: Any = PrivateAttr(default=None)

	def __init__(self, event_bus: EventBus, **kwargs):
		"""Initialize watchdog with event bus."""
		super().__init__(event_bus=event_bus, **kwargs)
		self._register_handlers()

	def _register_handlers(self) -> None:
		"""Register event handlers."""
		self.event_bus.on(BrowserStartedEvent, self._handle_browser_started)
		self.event_bus.on(BrowserStoppedEvent, self._handle_browser_stopped)
		self.event_bus.on(TabCreatedEvent, self._handle_tab_created)
		self.event_bus.on(TabClosedEvent, self._handle_tab_closed)

	async def _handle_browser_started(self, event: BrowserStartedEvent) -> None:
		"""Start monitoring when browser starts."""
		logger.info('[Watchdog] Browser started, beginning monitoring')
		# Note: Browser and context will be set by session via set_browser_context
		await self._start_monitoring()

	async def _handle_browser_stopped(self, event: BrowserStoppedEvent) -> None:
		"""Stop monitoring when browser stops."""
		logger.info('[Watchdog] Browser stopped, ending monitoring')
		await self._stop_monitoring()

	async def _handle_tab_created(self, event: TabCreatedEvent) -> None:
		"""Monitor new tabs."""
		# Tab will be added via add_page method from session
		pass

	async def _handle_tab_closed(self, event: TabClosedEvent) -> None:
		"""Stop monitoring closed tabs."""
		# Tab will be removed automatically via WeakSet
		pass

	def set_browser_context(self, browser: Browser, context: BrowserContext, browser_pid: int | None = None) -> None:
		"""Set browser and context references."""
		self._browser = browser
		self._browser_context = context
		self._browser_pid = browser_pid
		logger.info(f'[Watchdog] Browser and context references set (pid: {browser_pid})')

	def add_page(self, page: Page) -> None:
		"""Add a page to monitor."""
		self._pages.add(page)
		self._setup_page_listeners(page)
		logger.debug(f'[Watchdog] Added page to monitoring: {page.url}')

	def _setup_page_listeners(self, page: Page) -> None:
		"""Set up network and crash listeners for a page."""
		# Network request tracking
		page.on('request', lambda req: asyncio.create_task(self._on_request(req)))
		page.on('response', lambda resp: self._on_response(resp))
		page.on('requestfailed', lambda req: self._on_request_failed(req))
		page.on('requestfinished', lambda req: self._on_request_finished(req))

		# Page crash detection
		page.on('crash', lambda: asyncio.create_task(self._on_page_crash(page)))

	async def _on_request(self, request: Request) -> None:
		"""Track new network request."""
		request_id = f"{id(request)}"
		self._active_requests[request_id] = NetworkRequestTracker(
			request=request,
			start_time=time.time(),
			url=request.url,
			method=request.method,
			resource_type=request.resource_type,
		)
		logger.debug(f'[Watchdog] Tracking request: {request.method} {request.url[:50]}...')

	def _on_response(self, response: Response) -> None:
		"""Remove request from tracking on response."""
		request_id = f"{id(response.request)}"
		if request_id in self._active_requests:
			elapsed = time.time() - self._active_requests[request_id].start_time
			logger.debug(f'[Watchdog] Request completed in {elapsed:.2f}s: {response.url[:50]}...')
			del self._active_requests[request_id]

	def _on_request_failed(self, request: Request) -> None:
		"""Remove request from tracking on failure."""
		request_id = f"{id(request)}"
		if request_id in self._active_requests:
			elapsed = time.time() - self._active_requests[request_id].start_time
			logger.debug(f'[Watchdog] Request failed after {elapsed:.2f}s: {request.url[:50]}...')
			del self._active_requests[request_id]

	def _on_request_finished(self, request: Request) -> None:
		"""Remove request from tracking on finish."""
		request_id = f"{id(request)}"
		if request_id in self._active_requests:
			del self._active_requests[request_id]

	async def _on_page_crash(self, page: Page) -> None:
		"""Handle page crash."""
		logger.error(f'[Watchdog] Page crashed: {page.url}')
		
		# Find tab index
		tab_index = -1
		if self._browser_context:
			pages = self._browser_context.pages
			if page in pages:
				tab_index = pages.index(page)

		# Emit crash event
		self.event_bus.dispatch(
			PageCrashedEvent(
				tab_index=tab_index,
				error='Page crashed unexpectedly',
			)
		)

		# Also emit generic browser error
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='PageCrash',
				message=f'Page crashed: {page.url}',
				details={
					'url': page.url,
					'tab_index': tab_index,
				}
			)
		)

	async def _start_monitoring(self) -> None:
		"""Start the monitoring loop."""
		if self._monitoring_task and not self._monitoring_task.done():
			return

		self._monitoring_task = asyncio.create_task(self._monitoring_loop())
		logger.info('[Watchdog] Monitoring loop started')

		# Set up CDP session for browser crash detection
		if self._browser:
			try:
				# Get CDP session from browser
				cdp_contexts = await self._browser.contexts[0].new_cdp_session(self._browser.contexts[0].pages[0])
				self._cdp_session = cdp_contexts
				
				# Enable crash detection domains
				await self._cdp_session.send('Inspector.enable')
				await self._cdp_session.send('Page.enable')
				
				# Set up crash handlers
				self._cdp_session.on('Inspector.targetCrashed', self._on_browser_crash)
				
				logger.info('[Watchdog] CDP crash detection enabled')
			except Exception as e:
				logger.warning(f'[Watchdog] Failed to set up CDP crash detection: {e}')

	async def _stop_monitoring(self) -> None:
		"""Stop the monitoring loop."""
		if self._monitoring_task and not self._monitoring_task.done():
			self._monitoring_task.cancel()
			try:
				await self._monitoring_task
			except asyncio.CancelledError:
				pass
			logger.info('[Watchdog] Monitoring loop stopped')

		# Clean up CDP session
		if self._cdp_session:
			try:
				await self._cdp_session.detach()
			except Exception:
				pass
			self._cdp_session = None

		# Clear tracking
		self._active_requests.clear()
		self._pages.clear()

	async def _monitoring_loop(self) -> None:
		"""Main monitoring loop."""
		while True:
			try:
				await self._check_network_timeouts()
				await self._check_browser_health()
				await asyncio.sleep(self.check_interval_seconds)
			except asyncio.CancelledError:
				break
			except Exception as e:
				logger.error(f'[Watchdog] Error in monitoring loop: {e}')

	async def _check_network_timeouts(self) -> None:
		"""Check for network requests exceeding timeout."""
		current_time = time.time()
		timed_out_requests = []

		for request_id, tracker in self._active_requests.items():
			elapsed = current_time - tracker.start_time
			if elapsed > self.network_timeout_seconds:
				timed_out_requests.append((request_id, tracker))

		# Emit events for timed out requests
		for request_id, tracker in timed_out_requests:
			logger.warning(
				f'[Watchdog] Network request timeout after {self.network_timeout_seconds}s: '
				f'{tracker.method} {tracker.url[:100]}...'
			)
			
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NetworkTimeout',
					message=f'Network request timed out after {self.network_timeout_seconds}s',
					details={
						'url': tracker.url,
						'method': tracker.method,
						'resource_type': tracker.resource_type,
						'elapsed_seconds': current_time - tracker.start_time,
					}
				)
			)
			
			# Remove from tracking
			del self._active_requests[request_id]

	async def _check_browser_health(self) -> None:
		"""Check if browser and pages are still responsive."""
		# Check browser connection
		if self._browser and not self._browser.is_connected():
			logger.error('[Watchdog] Browser disconnected unexpectedly')
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='BrowserDisconnected',
					message='Browser disconnected unexpectedly',
					details={}
				)
			)
			await self._stop_monitoring()
			return

		# Check browser process if we have PID
		if hasattr(self, '_browser_pid') and self._browser_pid:
			try:
				import psutil
				proc = psutil.Process(self._browser_pid)
				if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
					logger.error(f'[Watchdog] Browser process {self._browser_pid} has crashed')
					self.event_bus.dispatch(
						BrowserErrorEvent(
							error_type='BrowserProcessCrashed',
							message=f'Browser process {self._browser_pid} has crashed',
							details={'pid': self._browser_pid, 'status': proc.status()}
						)
					)
					await self._stop_monitoring()
					return
			except Exception:
				pass  # psutil not available or process doesn't exist

		# Check each page
		dead_pages = []
		unresponsive_pages = []
		
		for page in list(self._pages):  # Copy to avoid modification during iteration
			try:
				if page.is_closed():
					dead_pages.append(page)
				# Check responsiveness for non-blank pages
				elif not self._is_new_tab_page(page.url):
					# Only check responsiveness occasionally to avoid overhead
					if not hasattr(page, '_last_responsive_check') or time.time() - page._last_responsive_check > 10:
						if not await self._is_page_responsive(page, timeout=2.0):
							unresponsive_pages.append(page)
						page._last_responsive_check = time.time()
			except Exception:
				# Page reference might be invalid
				dead_pages.append(page)

		# Remove dead pages
		for page in dead_pages:
			self._pages.discard(page)
		
		# Report unresponsive pages
		for page in unresponsive_pages:
			logger.warning(f'[Watchdog] Page unresponsive: {page.url}')
			
			# Find tab index
			tab_index = -1
			if self._browser_context:
				pages = self._browser_context.pages
				if page in pages:
					tab_index = pages.index(page)
			
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='PageUnresponsive',
					message=f'Page JS engine unresponsive: {page.url}',
					details={
						'url': page.url,
						'tab_index': tab_index,
					}
				)
			)
	
	async def _is_page_responsive(self, page: Page, timeout: float = 5.0) -> bool:
		"""Check if a page is responsive by trying to evaluate simple JavaScript.
		
		Reused from main branch BrowserSession._is_page_responsive
		"""
		eval_task = None
		try:
			eval_task = asyncio.create_task(page.evaluate('1'))
			done, pending = await asyncio.wait([eval_task], timeout=timeout)

			if eval_task in done:
				try:
					await eval_task  # This will raise if the evaluation failed
					return True
				except Exception:
					return False
			else:
				# Timeout - the page is unresponsive
				return False
		except Exception:
			return False
		finally:
			# Always clean up the eval task
			if eval_task and not eval_task.done():
				eval_task.cancel()
				try:
					await eval_task
				except (asyncio.CancelledError, Exception):
					pass
	
	def _is_new_tab_page(self, url: str) -> bool:
		"""Check if URL is a new tab page."""
		return url in ['about:blank', 'chrome://new-tab-page/', 'chrome://newtab/']

	def _on_browser_crash(self, params: Dict[str, Any]) -> None:
		"""Handle browser crash event from CDP."""
		logger.error('[Watchdog] Browser crash detected via CDP')
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='BrowserCrash',
				message='Browser process crashed',
				details=params
			)
		)

	async def trigger_browser_crash(self) -> None:
		"""Trigger a browser crash for testing (requires CDP session)."""
		if not self._cdp_session:
			logger.warning('[Watchdog] No CDP session available for crash testing')
			return

		try:
			logger.warning('[Watchdog] Triggering browser crash for testing...')
			await self._cdp_session.send('Browser.crash')
		except Exception as e:
			logger.error(f'[Watchdog] Failed to trigger browser crash: {e}')

	async def trigger_gpu_crash(self) -> None:
		"""Trigger a GPU process crash for testing (requires CDP session)."""
		if not self._cdp_session:
			logger.warning('[Watchdog] No CDP session available for GPU crash testing')
			return

		try:
			logger.warning('[Watchdog] Triggering GPU crash for testing...')
			await self._cdp_session.send('Browser.crashGpuProcess')
		except Exception as e:
			logger.error(f'[Watchdog] Failed to trigger GPU crash: {e}')

	async def trigger_page_crash(self, page: Page) -> None:
		"""Trigger a page crash for testing."""
		try:
			logger.warning(f'[Watchdog] Triggering page crash for testing on: {page.url}')
			cdp = await page.context.new_cdp_session(page)
			await cdp.send('Page.crash')
			await cdp.detach()
		except Exception as e:
			logger.error(f'[Watchdog] Failed to trigger page crash: {e}')