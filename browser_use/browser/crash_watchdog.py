"""Browser watchdog for monitoring crashes and network timeouts using CDP."""

import asyncio
import time
from typing import TYPE_CHECKING, ClassVar

import psutil
from bubus import BaseEvent
from pydantic import Field, PrivateAttr

from browser_use.browser.events import (
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserStoppedEvent,
	TargetCrashedEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog

if TYPE_CHECKING:
	pass


class NetworkRequestTracker:
	"""Tracks ongoing network requests."""

	def __init__(self, request_id: str, start_time: float, url: str, method: str, resource_type: str | None = None):
		self.request_id = request_id
		self.start_time = start_time
		self.url = url
		self.method = method
		self.resource_type = resource_type


class CrashWatchdog(BaseWatchdog):
	"""Monitors browser health for crashes and network timeouts using CDP."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		BrowserConnectedEvent,
		BrowserStoppedEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		BrowserErrorEvent,
		TargetCrashedEvent,
	]

	# Configuration
	network_timeout_seconds: float = Field(default=10.0)
	check_interval_seconds: float = Field(default=30.0)  # Reduced frequency to reduce noise

	# Private state
	_active_requests: dict[str, NetworkRequestTracker] = PrivateAttr(default_factory=dict)
	_monitoring_task: asyncio.Task | None = PrivateAttr(default=None)
	_last_responsive_checks: dict[str, float] = PrivateAttr(default_factory=dict)  # target_url -> timestamp
	_cdp_event_tasks: set[asyncio.Task] = PrivateAttr(default_factory=set)  # Track CDP event handler tasks
	_sessions_with_listeners: set[str] = PrivateAttr(default_factory=set)  # Track sessions that already have event listeners

	async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
		"""Start monitoring when browser is connected."""
		# logger.debug('[CrashWatchdog] Browser connected event received, beginning monitoring')

		await self._start_monitoring()
		# logger.debug(f'[CrashWatchdog] Monitoring task started: {self._monitoring_task and not self._monitoring_task.done()}')

	async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
		"""Stop monitoring when browser stops."""
		# logger.debug('[CrashWatchdog] Browser stopped, ending monitoring')
		await self._stop_monitoring()

	async def attach_to_target(self, target_id: str) -> None:
		"""Set up crash monitoring for a specific target using CDP."""
		try:
			# Get cached session (domains already enabled by get_cdp_session)
			cdp_client, session_id = await self.browser_session.get_cdp_session(target_id)
			
			# Check if we already have listeners for this session
			if session_id in self._sessions_with_listeners:
				self.logger.debug(f'[CrashWatchdog] Event listeners already exist for session: {session_id}')
				return

			# Set up network event handlers
			def on_request_will_be_sent(event):
				# Create and track the task
				task = asyncio.create_task(self._on_request_cdp(event))
				self._cdp_event_tasks.add(task)
				# Remove from set when done
				task.add_done_callback(lambda t: self._cdp_event_tasks.discard(t))

			def on_response_received(event):
				self._on_response_cdp(event)

			def on_loading_failed(event):
				self._on_request_failed_cdp(event)

			def on_loading_finished(event):
				self._on_request_finished_cdp(event)

			# Register event handlers
			# TEMPORARILY DISABLED: Network events causing too much logging
			# cdp_client.on('Network.requestWillBeSent', on_request_will_be_sent, session_id=session_id)
			# cdp_client.on('Network.responseReceived', on_response_received, session_id=session_id)
			# cdp_client.on('Network.loadingFailed', on_loading_failed, session_id=session_id)
			# cdp_client.on('Network.loadingFinished', on_loading_finished, session_id=session_id)

			# Set up crash handler (Inspector domain already enabled by get_cdp_session)
			def on_target_crashed(event):
				# Create and track the task
				task = asyncio.create_task(self._on_target_crash_cdp(target_id))
				self._cdp_event_tasks.add(task)
				# Remove from set when done
				task.add_done_callback(lambda t: self._cdp_event_tasks.discard(t))

			cdp_client.on('Inspector.targetCrashed', on_target_crashed, session_id=session_id)
			
			# Track that we've added listeners to this session
			self._sessions_with_listeners.add(session_id)

			# Get target info for logging
			targets = await cdp_client.send.Target.getTargets()
			target_info = next((t for t in targets['targetInfos'] if t['targetId'] == target_id), None)
			if target_info:
				self.logger.debug(f'[CrashWatchdog] Added target to monitoring: {target_info.get("url", "unknown")}')

		except Exception as e:
			self.logger.warning(f'[CrashWatchdog] Failed to attach to target {target_id}: {e}')

	async def _on_request_cdp(self, event: dict) -> None:
		"""Track new network request from CDP event."""
		request_id = event.get('requestId', '')
		request = event.get('request', {})

		self._active_requests[request_id] = NetworkRequestTracker(
			request_id=request_id,
			start_time=time.time(),
			url=request.get('url', ''),
			method=request.get('method', ''),
			resource_type=event.get('type'),
		)
		# logger.debug(f'[CrashWatchdog] Tracking request: {request.get("method", "")} {request.get("url", "")[:50]}...')

	def _on_response_cdp(self, event: dict) -> None:
		"""Remove request from tracking on response."""
		request_id = event.get('requestId', '')
		if request_id in self._active_requests:
			elapsed = time.time() - self._active_requests[request_id].start_time
			response = event.get('response', {})
			self.logger.debug(f'[CrashWatchdog] Request completed in {elapsed:.2f}s: {response.get("url", "")[:50]}...')
			# Don't remove yet - wait for loadingFinished

	def _on_request_failed_cdp(self, event: dict) -> None:
		"""Remove request from tracking on failure."""
		request_id = event.get('requestId', '')
		if request_id in self._active_requests:
			elapsed = time.time() - self._active_requests[request_id].start_time
			self.logger.debug(
				f'[CrashWatchdog] Request failed after {elapsed:.2f}s: {self._active_requests[request_id].url[:50]}...'
			)
			del self._active_requests[request_id]

	def _on_request_finished_cdp(self, event: dict) -> None:
		"""Remove request from tracking when loading is finished."""
		request_id = event.get('requestId', '')
		self._active_requests.pop(request_id, None)

	async def _on_target_crash_cdp(self, target_id: str) -> None:
		"""Handle target crash detected via CDP."""
		# Get target info
		cdp_client = self.browser_session.cdp_client
		targets = await cdp_client.send.Target.getTargets()
		target_info = next((t for t in targets['targetInfos'] if t['targetId'] == target_id), None)

		target_url = target_info.get('url', 'unknown') if target_info else 'unknown'
		self.logger.error(f'[CrashWatchdog] Target crashed: {target_url}')

		# Get tab index
		tab_index = await self.browser_session.get_tab_index(target_id)

		# Emit crash event
		self.event_bus.dispatch(
			TargetCrashedEvent(
				tab_index=tab_index,
				error='Target crashed unexpectedly',
			)
		)

		# Also emit generic browser error
		self.event_bus.dispatch(
			BrowserErrorEvent(
				error_type='TargetCrash',
				message=f'Target crashed: {target_url}',
				details={
					'url': target_url,
					'tab_index': tab_index,
					'target_id': target_id,
				},
			)
		)

	async def _start_monitoring(self) -> None:
		"""Start the monitoring loop."""
		assert self.browser_session.cdp_client is not None, 'Root CDP client not initialized - browser may not be connected yet'
		
		if self._monitoring_task and not self._monitoring_task.done():
			# logger.info('[CrashWatchdog] Monitoring already running')
			return

		self._monitoring_task = asyncio.create_task(self._monitoring_loop())
		# logger.debug('[CrashWatchdog] Monitoring loop created and started')

		# Set up CDP session for browser-level crash detection
		try:
			cdp_client = self.browser_session.cdp_client

			# Enable browser-level crash detection
			# Note: Browser domain might not be available in all contexts
			try:
				await cdp_client.send.Browser.getVersion()  # Test if Browser domain is available

				# Set up browser crash handler
				def on_browser_crash(event):
					self.logger.error('[CrashWatchdog] Browser crash detected via CDP')
					self.event_bus.dispatch(
						BrowserErrorEvent(error_type='BrowserCrash', message='Browser process crashed', details=event)
					)

				# Note: Browser.crash event might not exist, using Inspector.targetCrashed instead
				self.logger.debug('[CrashWatchdog] 💥 CDP crash detection enabled')
			except Exception:
				# Browser domain not available, rely on target-level monitoring
				pass

		except Exception as e:
			self.logger.warning(f'[CrashWatchdog] Failed to set up CDP crash detection: {e}')

	async def _stop_monitoring(self) -> None:
		"""Stop the monitoring loop."""
		if self._monitoring_task and not self._monitoring_task.done():
			self._monitoring_task.cancel()
			try:
				await self._monitoring_task
			except asyncio.CancelledError:
				pass
			self.logger.debug('[CrashWatchdog] Monitoring loop stopped')

		# Cancel all CDP event handler tasks
		for task in list(self._cdp_event_tasks):
			if not task.done():
				task.cancel()
		# Wait for all tasks to complete cancellation
		if self._cdp_event_tasks:
			await asyncio.gather(*self._cdp_event_tasks, return_exceptions=True)
		self._cdp_event_tasks.clear()

		# Clear tracking (CDP sessions are cached and managed by BrowserSession)
		self._active_requests.clear()
		self._sessions_with_listeners.clear()

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
				self.logger.error(f'[CrashWatchdog] Error in monitoring loop: {e}')

	async def _check_network_timeouts(self) -> None:
		"""Check for network requests exceeding timeout."""
		current_time = time.time()
		timed_out_requests = []

		# Debug logging
		if self._active_requests:
			self.logger.debug(
				f'[CrashWatchdog] Checking {len(self._active_requests)} active requests for timeouts (threshold: {self.network_timeout_seconds}s)'
			)

		for request_id, tracker in self._active_requests.items():
			elapsed = current_time - tracker.start_time
			self.logger.debug(
				f'[CrashWatchdog] Request {tracker.url[:30]}... elapsed: {elapsed:.1f}s, timeout: {self.network_timeout_seconds}s'
			)
			if elapsed >= self.network_timeout_seconds:
				timed_out_requests.append((request_id, tracker))

		# Emit events for timed out requests
		for request_id, tracker in timed_out_requests:
			self.logger.warning(
				f'[CrashWatchdog] Network request timeout after {self.network_timeout_seconds}s: '
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
					},
				)
			)

			# Remove from tracking
			del self._active_requests[request_id]

	async def _check_browser_health(self) -> None:
		"""Check if browser and targets are still responsive."""
		# Check browser connection via CDP
		try:
			cdp_client = self.browser_session.cdp_client
			# Try a simple CDP command to check connection
			await asyncio.wait_for(cdp_client.send.Target.getTargets(), timeout=2.0)
		except (asyncio.TimeoutError, Exception) as e:
			self.logger.error(f'[CrashWatchdog] Browser connection check failed: {e}')
			self.event_bus.dispatch(
				BrowserErrorEvent(error_type='BrowserDisconnected', message='Browser disconnected unexpectedly', details={})
			)
			# Exit the monitoring loop
			raise asyncio.CancelledError('Browser disconnected')
		
		# Check all cached CDP sessions for health
		if hasattr(self.browser_session, '_cdp_session_cache'):
			dead_sessions = []
			for target_id, cached_value in list(self.browser_session._cdp_session_cache.items()):
				try:
					client, session_id = cached_value
					# Quick ping to check if session is alive
					await asyncio.wait_for(
						client.send.Runtime.evaluate(params={'expression': '1'}, session_id=session_id),
						timeout=1.0
					)
				except:
					dead_sessions.append(target_id)
					self.logger.warning(f'[CrashWatchdog] Dead session detected for target {target_id}')
			
			# Clean up dead sessions
			for target_id in dead_sessions:
				await self.browser_session._cdp_release_session(target_id)

		# Check browser process if we have PID
		if proc := self.browser_session._local_browser_watchdog._subprocess:
			try:
				if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
					self.logger.error(f'[CrashWatchdog] Browser process {proc.pid} has crashed')
					self.event_bus.dispatch(
						BrowserErrorEvent(
							error_type='BrowserProcessCrashed',
							message=f'Browser process {proc.pid} has crashed',
							details={'pid': proc.pid, 'status': proc.status()},
						)
					)
					await self._stop_monitoring()
					return
			except Exception:
				pass  # psutil not available or process doesn't exist

		# Check each target
		dead_targets = []
		unresponsive_targets = []

		try:
			targets = await cdp_client.send.Target.getTargets()
			target_infos = targets.get('targetInfos', [])

			# Only check page-type targets
			target_list = [t for t in target_infos if t.get('type') == 'page']

			for target in target_list:
				target_id = target['targetId']
				target_url = target.get('url', '')

				# Skip new tab pages
				if self._is_new_tab_page(target_url):
					continue

				# Check if target is still attached/alive
				try:
					# Check responsiveness occasionally to avoid overhead
					last_check = self._last_responsive_checks.get(target_url, 0)
					if time.time() - last_check > 10:
						if not await self._is_target_responsive(target_id, timeout=2.0):
							unresponsive_targets.append(target)
						self._last_responsive_checks[target_url] = time.time()
				except Exception:
					# Target might be dead
					dead_targets.append(target)

		except Exception as e:
			self.logger.error(f'[CrashWatchdog] Error checking target health: {e}')
			return

		# Report unresponsive targets
		for target in unresponsive_targets:
			target_url = target.get('url', 'unknown')
			self.logger.warning(f'[CrashWatchdog] Target unresponsive: {target_url}')

			tab_index = await self.browser_session.get_tab_index(target['targetId'])
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='TargetUnresponsive',
					message=f'Target JS engine unresponsive: {target_url}',
					details={
						'url': target_url,
						'tab_index': tab_index,
						'target_id': target['targetId'],
					},
				)
			)

	async def _is_target_responsive(self, target_id: str, timeout: float = 5.0) -> bool:
		"""Check if a target is responsive by trying to evaluate simple JavaScript."""
		eval_task = None
		try:
			# Get cached session
			cdp_client, session_id = await self.browser_session.get_cdp_session(target_id)

			# Try to evaluate simple JavaScript
			eval_task = asyncio.create_task(cdp_client.send.Runtime.evaluate(params={'expression': '1'}, session_id=session_id))
			done, pending = await asyncio.wait([eval_task], timeout=timeout)

			if eval_task in done:
				try:
					result = await eval_task
					# Check if evaluation succeeded
					if result.get('result', {}).get('value') == 1:
						return True
				except Exception:
					return False
			else:
				# Timeout - the target is unresponsive
				return False
		except Exception:
			return False
		finally:
			# Clean up the eval task
			if eval_task and not eval_task.done():
				eval_task.cancel()
				try:
					await eval_task
				except (asyncio.CancelledError, Exception):
					pass

			# No need to detach - sessions are cached and managed by BrowserSession

	@staticmethod
	def _is_new_tab_page(url: str) -> bool:
		"""Check if URL is a new tab page."""
		return url in ['about:blank', 'chrome://new-tab-page/', 'chrome://newtab/']

	async def trigger_browser_crash(self) -> None:
		"""Trigger a browser crash for testing (requires CDP)."""
		try:
			cdp_client = self.browser_session.cdp_client
			self.logger.warning('[CrashWatchdog] Triggering browser crash for testing...')
			await cdp_client.send.Browser.crash()
		except Exception as e:
			self.logger.error(f'[CrashWatchdog] Failed to trigger browser crash: {e}')

	async def trigger_gpu_crash(self) -> None:
		"""Trigger a GPU process crash for testing (requires CDP)."""
		try:
			cdp_client = self.browser_session.cdp_client
			self.logger.warning('[CrashWatchdog] Triggering GPU crash for testing...')
			await cdp_client.send.Browser.crashGpuProcess()
		except Exception as e:
			self.logger.error(f'[CrashWatchdog] Failed to trigger GPU crash: {e}')

	async def trigger_target_crash(self, target_id: str) -> None:
		"""Trigger a target crash for testing."""
		try:
			cdp_client = self.browser_session.cdp_client

			# Get target info for logging
			targets = await cdp_client.send.Target.getTargets()
			target_info = next((t for t in targets['targetInfos'] if t['targetId'] == target_id), None)
			target_url = target_info.get('url', 'unknown') if target_info else 'unknown'

			self.logger.warning(f'[CrashWatchdog] Triggering target crash for testing on: {target_url}')

			# Get cached session
			cdp_client, session_id = await self.browser_session.get_cdp_session(target_id)

			# Crash the target
			await cdp_client.send.Page.crash(session_id=session_id)
		except Exception as e:
			self.logger.error(f'[CrashWatchdog] Failed to trigger target crash: {e}')
