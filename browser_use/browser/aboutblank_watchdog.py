"""About:blank watchdog for managing about:blank tabs with DVD screensaver."""

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent
from playwright.async_api import Page

from browser_use.browser.events import (
	BrowserStoppedEvent,
	CloseTabEvent,
	NavigateToUrlEvent,
	TabClosedEvent,
	TabCreatedEvent,
	TabsInfoRequestEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.utils import logger

if TYPE_CHECKING:
	pass


class AboutBlankWatchdog(BaseWatchdog):
	"""Ensures there's always exactly one about:blank tab with DVD screensaver."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		BrowserStoppedEvent,
		TabCreatedEvent,
		TabClosedEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		NavigateToUrlEvent,
		CloseTabEvent,
		TabsInfoRequestEvent,
	]

	async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
		"""Handle browser stopped event."""
		logger.info('[AboutBlankWatchdog] Browser stopped')

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		"""Check tabs when a new tab is created."""
		logger.debug(f'[AboutBlankWatchdog] Tab created: {event.url}')
		# Small delay to let tab finish loading
		await asyncio.sleep(0.1)
		await self._check_and_ensure_about_blank_tab()

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		"""Check tabs when a tab is closed and proactively create about:blank if needed."""
		logger.debug('[AboutBlankWatchdog] Tab closed, checking if we need to create about:blank tab')

		# Check if we're about to close the last page
		pages = self.browser_session.pages
		if len(pages) <= 1:
			logger.info('[AboutBlankWatchdog] Last page closing, proactively creating about:blank tab')
			navigate_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
			await navigate_event
			# Wait a bit for navigation to complete
			await asyncio.sleep(0.1)
			# Show DVD screensaver on the new tab
			await self._show_dvd_screensaver_on_about_blank_tabs()
		else:
			# Normal check after tab closes
			await asyncio.sleep(0.1)
			await self._check_and_ensure_about_blank_tab()

	async def attach_to_page(self, page: Page) -> None:
		"""AboutBlankWatchdog doesn't monitor individual pages."""
		pass

	async def _check_and_ensure_about_blank_tab(self) -> None:
		"""Check current tabs and ensure exactly one about:blank tab with animation exists."""
		try:
			pages = self.browser_session.pages

			# Only look for tabs that have our animation (check by title)
			animation_pages = []
			browser_session_id = str(self.browser_session.id)[-4:]
			expected_title = f'Starting agent {browser_session_id}...'

			for page in pages:
				if page.url == 'about:blank':
					try:
						page_title = await page.title()
						if page_title == expected_title:
							animation_pages.append(page)
					except Exception:
						pass  # Skip pages that can't be checked

			logger.debug(f'[AboutBlankWatchdog] Found {len(animation_pages)} animation tabs out of {len(pages)} total tabs')

			# If no animation tabs exist, create one
			if not animation_pages:
				logger.info('[AboutBlankWatchdog] Creating animation tab')
				event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
				await event
				# Wait a bit for navigation to complete
				await asyncio.sleep(0.1)
				# Show DVD screensaver on the new tab
				await self._show_dvd_screensaver_on_about_blank_tabs()
			# If more than one animation tab exists, close the extras (only the ones with animation)
			elif len(animation_pages) > 1:
				logger.info(
					f'[AboutBlankWatchdog] Found {len(animation_pages)} animation tabs, closing {len(animation_pages) - 1} extras'
				)
				# Keep the first animation tab, close the rest
				for i in range(1, len(animation_pages)):
					page = animation_pages[i]
					# Find the tab index for this page
					tab_index = None
					for idx, p in enumerate(pages):
						if p == page:
							tab_index = idx
							break
					if tab_index is not None:
						event = self.event_bus.dispatch(CloseTabEvent(tab_index=tab_index))
						await event
						await asyncio.sleep(0.1)  # Small delay between closes

		except Exception as e:
			logger.error(f'[AboutBlankWatchdog] Error ensuring about:blank tab: {e}')

	async def _show_dvd_screensaver_on_about_blank_tabs(self) -> None:
		"""Show DVD screensaver on all about:blank tabs."""
		try:
			pages = self.browser_session.pages
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
