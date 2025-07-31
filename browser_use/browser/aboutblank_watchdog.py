"""About:blank watchdog for managing about:blank tabs with DVD screensaver."""

import asyncio
from typing import Any

from bubus import EventBus
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.events import (
	BrowserStartedEvent,
	BrowserStoppedEvent,
	CloseTabEvent,
	CreateTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
	TabsInfoRequestEvent,
	TabsInfoResponseEvent,
)
from browser_use.utils import logger


class AboutBlankWatchdog(BaseModel):
	"""Ensures there's always exactly one about:blank tab with DVD screensaver."""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	event_bus: EventBus
	browser_session: Any  # Avoid circular import
	check_interval_seconds: float = Field(default=2.0)

	# Only keep the monitoring task as private state
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

	async def _handle_browser_started(self, event: BrowserStartedEvent) -> None:
		"""Start monitoring when browser starts."""
		logger.info('[AboutBlankWatchdog] Browser started, beginning monitoring')
		await self._start_monitoring()
		# Check tabs immediately
		await self._check_and_ensure_about_blank_tab()

	async def _handle_browser_stopped(self, event: BrowserStoppedEvent) -> None:
		"""Stop monitoring when browser stops."""
		logger.info('[AboutBlankWatchdog] Browser stopped, ending monitoring')
		await self._stop_monitoring()

	async def _handle_tab_created(self, event: TabCreatedEvent) -> None:
		"""Check tabs when a new tab is created."""
		logger.debug(f'[AboutBlankWatchdog] Tab created: {event.url}')
		# Small delay to let tab finish loading
		await asyncio.sleep(0.5)
		await self._check_and_ensure_about_blank_tab()

	async def _handle_tab_closed(self, event: TabClosedEvent) -> None:
		"""Check tabs when a tab is closed."""
		logger.debug('[AboutBlankWatchdog] Tab closed')
		# Small delay to let tab finish closing
		await asyncio.sleep(0.5)
		await self._check_and_ensure_about_blank_tab()

	async def _start_monitoring(self) -> None:
		"""Start the monitoring task."""
		if self._monitoring_task and not self._monitoring_task.done():
			return
		self._monitoring_task = asyncio.create_task(self._monitor_tabs())
		logger.info('[AboutBlankWatchdog] Started monitoring task')

	async def _stop_monitoring(self) -> None:
		"""Stop the monitoring task."""
		if self._monitoring_task and not self._monitoring_task.done():
			self._monitoring_task.cancel()
			try:
				await self._monitoring_task
			except asyncio.CancelledError:
				pass
			logger.info('[AboutBlankWatchdog] Stopped monitoring task')

	async def _monitor_tabs(self) -> None:
		"""Periodically check and ensure about:blank tab."""
		while True:
			try:
				await asyncio.sleep(self.check_interval_seconds)
				await self._check_and_ensure_about_blank_tab()
			except asyncio.CancelledError:
				break
			except Exception as e:
				logger.error(f'[AboutBlankWatchdog] Error in monitoring loop: {e}')

	async def _get_current_tabs(self) -> list[dict[str, Any]]:
		"""Get current tabs info using event system."""
		# Request tabs info
		self.event_bus.dispatch(TabsInfoRequestEvent())

		# Wait for response
		try:
			event_result = await self.event_bus.expect(TabsInfoResponseEvent, timeout=5.0)
			response: TabsInfoResponseEvent = event_result  # type: ignore
			return response.tabs
		except TimeoutError:
			logger.warning('[AboutBlankWatchdog] Timeout waiting for tabs info')
			return []

	async def _check_and_ensure_about_blank_tab(self) -> None:
		"""Check current tabs and ensure exactly one about:blank tab exists."""
		try:
			tabs = await self._get_current_tabs()
			if not tabs:
				return

			# Count about:blank tabs
			about_blank_tabs = [tab for tab in tabs if tab['url'] == 'about:blank']
			other_tabs = [tab for tab in tabs if tab['url'] != 'about:blank']

			logger.debug(f'[AboutBlankWatchdog] Found {len(about_blank_tabs)} about:blank tabs and {len(other_tabs)} other tabs')

			# If no about:blank tabs, create one
			if not about_blank_tabs:
				logger.info('[AboutBlankWatchdog] No about:blank tab found, creating one')
				event = self.event_bus.dispatch(CreateTabEvent(url='about:blank'))
				await event
				# Wait a bit for navigation to complete
				await asyncio.sleep(1.0)
				# Show DVD screensaver on the new tab
				await self._show_dvd_screensaver_on_about_blank_tabs()

			# If more than one about:blank tab, close extras
			elif len(about_blank_tabs) > 1:
				logger.info(f'[AboutBlankWatchdog] Found {len(about_blank_tabs)} about:blank tabs, closing extras')
				# Keep the first one, close the rest
				for i in range(1, len(about_blank_tabs)):
					tab = about_blank_tabs[i]
					event = self.event_bus.dispatch(CloseTabEvent(tab_index=tab['index']))
					await event
					await asyncio.sleep(0.2)  # Small delay between closes

			# Ensure the about:blank tab has the screensaver
			else:
				await self._show_dvd_screensaver_on_about_blank_tabs()

		except Exception as e:
			logger.error(f'[AboutBlankWatchdog] Error ensuring about:blank tab: {e}')

	async def _show_dvd_screensaver_on_about_blank_tabs(self) -> None:
		"""Show DVD screensaver on all about:blank tabs."""
		try:
			# Get current page from browser session
			if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
				return

			pages = self.browser_session._browser_context.pages
			browser_session_id = str(self.browser_session.id)[-4:]

			for page in pages:
				if page.url == 'about:blank':
					await self._show_dvd_screensaver_loading_animation(page, browser_session_id)

		except Exception as e:
			logger.error(f'[AboutBlankWatchdog] Error showing DVD screensaver: {e}')

	async def _show_dvd_screensaver_loading_animation(self, page: Any, browser_session_label: str) -> None:
		"""
		Injects a DVD screensaver-style bouncing logo loading animation overlay into the given Playwright Page.
		This is used to visually indicate that the browser is setting up or waiting.
		"""
		try:
			await page.evaluate(
				"""(browser_session_label) => {
				// Ensure document.body exists before proceeding
				if (!document.body) {
					// Try again after DOM is ready
					if (document.readyState === 'loading') {
						document.addEventListener('DOMContentLoaded', () => arguments.callee(browser_session_label));
					}
					return;
				}
				
				const animated_title = `Starting agent ${browser_session_label}...`;
				if (document.title === animated_title) {
					return;      // already run on this tab, dont run again
				}
				document.title = animated_title;

				// Create the main overlay
				const loadingOverlay = document.createElement('div');
				loadingOverlay.id = 'pretty-loading-animation';
				loadingOverlay.style.position = 'fixed';
				loadingOverlay.style.top = '0';
				loadingOverlay.style.left = '0';
				loadingOverlay.style.width = '100vw';
				loadingOverlay.style.height = '100vh';
				loadingOverlay.style.background = '#000';
				loadingOverlay.style.zIndex = '99999';
				loadingOverlay.style.overflow = 'hidden';

				// Create the image element
				const img = document.createElement('img');
				img.src = 'https://cf.browser-use.com/logo.svg';
				img.alt = 'Browser-Use';
				img.style.width = '200px';
				img.style.height = 'auto';
				img.style.position = 'absolute';
				img.style.left = '0px';
				img.style.top = '0px';
				img.style.zIndex = '2';
				img.style.opacity = '0.8';

				loadingOverlay.appendChild(img);
				document.body.appendChild(loadingOverlay);

				// DVD screensaver bounce logic
				let x = Math.random() * (window.innerWidth - 300);
				let y = Math.random() * (window.innerHeight - 300);
				let dx = 1.2 + Math.random() * 0.4; // px per frame
				let dy = 1.2 + Math.random() * 0.4;
				// Randomize direction
				if (Math.random() > 0.5) dx = -dx;
				if (Math.random() > 0.5) dy = -dy;

				function animate() {
					const imgWidth = img.offsetWidth || 300;
					const imgHeight = img.offsetHeight || 300;
					x += dx;
					y += dy;

					if (x <= 0) {
						x = 0;
						dx = Math.abs(dx);
					} else if (x + imgWidth >= window.innerWidth) {
						x = window.innerWidth - imgWidth;
						dx = -Math.abs(dx);
					}
					if (y <= 0) {
						y = 0;
						dy = Math.abs(dy);
					} else if (y + imgHeight >= window.innerHeight) {
						y = window.innerHeight - imgHeight;
						dy = -Math.abs(dy);
					}

					img.style.left = `${x}px`;
					img.style.top = `${y}px`;

					requestAnimationFrame(animate);
				}
				animate();

				// Responsive: update bounds on resize
				window.addEventListener('resize', () => {
					x = Math.min(x, window.innerWidth - img.offsetWidth);
					y = Math.min(y, window.innerHeight - img.offsetHeight);
				});

				// Add a little CSS for smoothness
				const style = document.createElement('style');
				style.textContent = `
					#pretty-loading-animation {
						/*backdrop-filter: blur(2px) brightness(0.9);*/
					}
					#pretty-loading-animation img {
						user-select: none;
						pointer-events: none;
					}
				`;
				document.head.appendChild(style);
			}""",
				browser_session_label,
			)
		except Exception as e:
			logger.debug(f'[AboutBlankWatchdog] Failed to show DVD loading animation: {type(e).__name__}: {e}')
