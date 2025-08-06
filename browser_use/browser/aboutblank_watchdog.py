"""About:blank watchdog for managing about:blank tabs with DVD screensaver."""

from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent
from playwright.async_api import Page
from pydantic import PrivateAttr

from browser_use.browser.crash_watchdog import CrashWatchdog
from browser_use.browser.events import (
	AboutBlankDVDScreensaverShownEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
	CloseTabEvent,
	NavigateToUrlEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.utils import logger

if TYPE_CHECKING:
	pass


class AboutBlankWatchdog(BaseWatchdog):
	"""Ensures there's always exactly one about:blank tab with DVD screensaver."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		BrowserStopEvent,
		BrowserStoppedEvent,
		TabCreatedEvent,
		TabClosedEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		NavigateToUrlEvent,
		CloseTabEvent,
		AboutBlankDVDScreensaverShownEvent,
	]

	_stopping: bool = PrivateAttr(default=False)

	async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
		"""Handle browser stop request - stop creating new tabs."""
		# logger.info('[AboutBlankWatchdog] Browser stop requested, stopping tab creation')
		self._stopping = True

	async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
		"""Handle browser stopped event."""
		# logger.info('[AboutBlankWatchdog] Browser stopped')
		self._stopping = True

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		"""Check tabs when a new tab is created."""
		# logger.debug(f'[AboutBlankWatchdog] ➕ New tab created: {event.url}')

		# If a new tab page was created, show DVD screensaver on it
		if CrashWatchdog._is_new_tab_page(event.url):
			await self._show_dvd_screensaver_on_about_blank_tabs()
		else:
			await self._check_and_ensure_about_blank_tab()

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		"""Check tabs when a tab is closed and proactively create about:blank if needed."""
		# logger.debug('[AboutBlankWatchdog] Tab closing, checking if we need to create about:blank tab')

		# Don't create new tabs if browser is shutting down
		if self._stopping:
			# logger.debug('[AboutBlankWatchdog] Browser is stopping, not creating new tabs')
			return

		# Check if we're about to close the last tab (event happens BEFORE tab closes)
		targets = await self.browser_session.cdp_client.send.Target.getTargets()
		page_targets = [t for t in targets.get('targetInfos', []) if t.get('type') == 'page']
		if len(page_targets) <= 1:
			logger.info('[AboutBlankWatchdog] Last tab closing, creating new about:blank tab to avoid closing entire browser')
			# Create the animation tab since no tabs should remain
			navigate_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
			await navigate_event
			# Show DVD screensaver on the new tab
			await self._show_dvd_screensaver_on_about_blank_tabs()
		else:
			# Multiple tabs exist, check after close
			await self._check_and_ensure_about_blank_tab()

	async def attach_to_page(self, page: Page) -> None:
		"""AboutBlankWatchdog doesn't monitor individual pages."""
		pass

	async def _check_and_ensure_about_blank_tab(self) -> None:
		"""Check current tabs and ensure exactly one about:blank tab with animation exists."""
		try:
			targets = await self.browser_session.cdp_client.send.Target.getTargets()
			page_targets = [t for t in targets.get('targetInfos', []) if t.get('type') == 'page']

			# Only look for tabs that have our animation (check by title)
			animation_targets = []
			browser_session_id = str(self.browser_session.id)[-4:]
			expected_title = f'Starting agent {browser_session_id}...'

			for target in page_targets:
				if CrashWatchdog._is_new_tab_page(target.get('url', '')):
					try:
						# Get title using CDP
						target_id = target['targetId']
						session = await self.browser_session.cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
						session_id = session['sessionId']
						title_result = await self.browser_session.cdp_client.send.Runtime.evaluate(
							params={'expression': 'document.title'},
							session_id=session_id
						)
						page_title = title_result.get('result', {}).get('value', '')
						await self.browser_session.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})
						
						if page_title == expected_title:
							animation_targets.append(target)
					except Exception:
						pass  # Skip targets that can't be checked

			# logger.debug(f'[AboutBlankWatchdog] Found {len(animation_targets)} animation tabs out of {len(page_targets)} total tabs')

			# If no animation tabs exist, create one only if there are no tabs at all
			if not animation_targets:
				if len(page_targets) == 0:
					# Only create a new tab if there are no tabs at all
					# logger.info('[AboutBlankWatchdog] No tabs exist, creating new about:blank DVD screensaver tab')
					navigate_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
					await navigate_event
					# Show DVD screensaver on the new tab
					await self._show_dvd_screensaver_on_about_blank_tabs()
				else:
					# There are other tabs - don't create new about:blank tabs during scripting
					# logger.debug(
					# 	f'[AboutBlankWatchdog] {len(page_targets)} tabs exist, not creating animation tab to avoid interfering with scripting'
					# )
					pass
			# If more than one animation tab exists, just log it - don't close anything
			elif len(animation_targets) > 1:
				# logger.debug(f'[AboutBlankWatchdog] Found {len(animation_targets)} animation tabs, allowing them to exist')
				pass

		except Exception as e:
			logger.error(f'[AboutBlankWatchdog] Error ensuring about:blank tab: {e}')

	async def _show_dvd_screensaver_on_about_blank_tabs(self) -> None:
		"""Show DVD screensaver on all new tab pages."""
		try:
			targets = await self.browser_session.cdp_client.send.Target.getTargets()
			page_targets = [t for t in targets.get('targetInfos', []) if t.get('type') == 'page']
			browser_session_id = str(self.browser_session.id)[-4:]

			for target in page_targets:
				url = target.get('url', '')
				if CrashWatchdog._is_new_tab_page(url):
					target_id = target['targetId']
					# If it's a chrome:// new tab page, redirect to about:blank to avoid CSP issues
					if url.startswith('chrome://'):
						# Navigate using CDP
						session = await self.browser_session.cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
						session_id = session['sessionId']
						await self.browser_session.cdp_client.send.Page.navigate(params={'url': 'about:blank'}, session_id=session_id)
						await self.browser_session.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})
					await self._show_dvd_screensaver_loading_animation_cdp(target_id, browser_session_id)

		except Exception as e:
			logger.error(f'[AboutBlankWatchdog] Error showing DVD screensaver: {e}')

	async def _show_dvd_screensaver_loading_animation(self, page: Any, browser_session_label: str) -> None:
		"""
		Injects a DVD screensaver-style bouncing logo loading animation overlay into the given Playwright Page.
		This is used to visually indicate that the browser is setting up or waiting.
		"""
		# Get tab index for the event
		tab_index = self.browser_session.get_tab_index(page)

		try:
			await page.evaluate(
				"""function(browser_session_label) {
				// Ensure document.body exists before proceeding
				if (!document.body) {
					// Try again after DOM is ready
					if (document.readyState === 'loading') {
						document.addEventListener('DOMContentLoaded', function() { arguments.callee(browser_session_label); });
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

			# Emit success event
			self.event_bus.dispatch(AboutBlankDVDScreensaverShownEvent(tab_index=tab_index, error=None))

		except Exception as e:
			logger.debug(f'[AboutBlankWatchdog] Failed to show DVD loading animation: {type(e).__name__}: {e}')

			# Emit error event
			self.event_bus.dispatch(AboutBlankDVDScreensaverShownEvent(tab_index=tab_index, error=str(e)))
