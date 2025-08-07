"""About:blank watchdog for managing about:blank tabs with DVD screensaver."""

from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent
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

		# If an about:blank tab was created, show DVD screensaver on all about:blank tabs
		if event.url == 'about:blank':
			await self._show_dvd_screensaver_on_about_blank_tabs()

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		"""Check tabs when a tab is closed and proactively create about:blank if needed."""
		# logger.debug('[AboutBlankWatchdog] Tab closing, checking if we need to create about:blank tab')

		# Don't create new tabs if browser is shutting down
		if self._stopping:
			# logger.debug('[AboutBlankWatchdog] Browser is stopping, not creating new tabs')
			return

		# Check if we're about to close the last tab (event happens BEFORE tab closes)
		# Use _cdp_get_all_pages for quick check without fetching titles
		page_targets = await self.browser_session._cdp_get_all_pages()
		if len(page_targets) <= 1:
			self.logger.info('[AboutBlankWatchdog] Last tab closing, creating new about:blank tab to avoid closing entire browser')
			# Create the animation tab since no tabs should remain
			navigate_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
			await navigate_event
			# Show DVD screensaver on the new tab
			await self._show_dvd_screensaver_on_about_blank_tabs()
		else:
			# Multiple tabs exist, check after close
			await self._check_and_ensure_about_blank_tab()

	async def attach_to_target(self, target_id: str) -> None:
		"""AboutBlankWatchdog doesn't monitor individual targets."""
		pass

	async def _check_and_ensure_about_blank_tab(self) -> None:
		"""Check current tabs and ensure exactly one about:blank tab with animation exists."""
		try:
			# For quick checks, just get page targets without titles to reduce noise
			page_targets = await self.browser_session._cdp_get_all_pages()
			
			# Only get full tabs info if we actually need to check titles
			tabs_info = None
			
			# For AboutBlankWatchdog, we only care about tab count not titles
			# Only get full tabs info if we actually have animation tabs to check
			if len(page_targets) == 0:
				tabs_info = []
			else:
				# Skip the expensive get_tabs_info call - we just need tab count
				tabs_info = None
			
			# We don't need to track animation tabs anymore since we're only ensuring
			# that there's at least one tab open
			animation_tabs = []

			# logger.debug(f'[AboutBlankWatchdog] Found {len(animation_tabs)} animation tabs out of {len(page_targets)} total tabs')

			# If no animation tabs exist, create one only if there are no tabs at all
			if not animation_tabs:
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
					# 	f'[AboutBlankWatchdog] {len(tabs_info)} tabs exist, not creating animation tab to avoid interfering with scripting'
					# )
					pass
			# If more than one animation tab exists, just log it - don't close anything
			elif len(animation_tabs) > 1:
				# logger.debug(f'[AboutBlankWatchdog] Found {len(animation_tabs)} animation tabs, allowing them to exist')
				pass

		except Exception as e:
			self.logger.error(f'[AboutBlankWatchdog] Error ensuring about:blank tab: {e}')

	async def _show_dvd_screensaver_on_about_blank_tabs(self) -> None:
		"""Show DVD screensaver on all about:blank pages only."""
		try:
			# Get just the page targets without expensive title fetching
			page_targets = await self.browser_session._cdp_get_all_pages()
			browser_session_id = str(self.browser_session.id)[-4:]

			for page_target in page_targets:
				target_id = page_target['targetId']
				url = page_target['url']
				
				# Only target about:blank pages specifically
				if url == 'about:blank':
					await self._show_dvd_screensaver_loading_animation_cdp(target_id, browser_session_id)

		except Exception as e:
			self.logger.error(f'[AboutBlankWatchdog] Error showing DVD screensaver: {e}')

	async def _show_dvd_screensaver_loading_animation_cdp(self, target_id: str, browser_session_label: str) -> None:
		"""
		Injects a DVD screensaver-style bouncing logo loading animation overlay into the target using CDP.
		This is used to visually indicate that the browser is setting up or waiting.
		"""
		try:
			# Attach to target
			session = await self.browser_session.cdp_client.send.Target.attachToTarget(
				params={'targetId': target_id, 'flatten': True}
			)
			session_id = session['sessionId']
			
			# Inject the DVD screensaver script
			script = f"""
				(function(browser_session_label) {{
					// Check if animation is already running using window property
					if (window.__dvdAnimationRunning) {{
						return; // Already running, don't add another
					}}
					window.__dvdAnimationRunning = true;
					
					// Ensure document.body exists before proceeding
					if (!document.body) {{
						// Try again after DOM is ready
						window.__dvdAnimationRunning = false; // Reset flag to retry
						if (document.readyState === 'loading') {{
							document.addEventListener('DOMContentLoaded', function() {{ arguments.callee(browser_session_label); }});
						}}
						return;
					}}
					
					const animated_title = `Starting agent ${{browser_session_label}}...`;
					document.title = animated_title;

					// Create the main overlay
					const loadingOverlay = document.createElement('div');
					loadingOverlay.id = 'pretty-loading-animation';
					loadingOverlay.style.position = 'fixed';
					loadingOverlay.style.top = '0';
					loadingOverlay.style.left = '0';
					loadingOverlay.style.width = '100vw';
					loadingOverlay.style.height = '100vh';
					loadingOverlay.style.backgroundColor = '#1e1e1e';
					loadingOverlay.style.backgroundImage = 'radial-gradient(ellipse at top left, #2a2a3e 0%, #1e1e1e 50%, #0f0f0f 100%)';
					loadingOverlay.style.zIndex = '1000000';
					loadingOverlay.style.display = 'flex';
					loadingOverlay.style.justifyContent = 'center';
					loadingOverlay.style.alignItems = 'center';
					loadingOverlay.style.overflow = 'hidden';

					// Create the loading box (DVD logo)
					const loadingBox = document.createElement('div');
					loadingBox.id = 'dvd-logo';
					loadingBox.style.position = 'absolute';
					loadingBox.style.width = '160px';
					loadingBox.style.height = '80px';
					loadingBox.style.backgroundColor = '#007ACC';
					loadingBox.style.borderRadius = '15px';
					loadingBox.style.display = 'flex';
					loadingBox.style.flexDirection = 'column';
					loadingBox.style.justifyContent = 'center';
					loadingBox.style.alignItems = 'center';
					loadingBox.style.boxShadow = '0 0 30px rgba(0, 122, 204, 0.7), inset 0 0 20px rgba(255, 255, 255, 0.2)';
					loadingBox.style.transition = 'background-color 0.3s ease, box-shadow 0.3s ease';

					// Add logo emoji
					const logoEmoji = document.createElement('div');
					logoEmoji.innerHTML = '🅰';
					logoEmoji.style.fontSize = '32px';
					logoEmoji.style.marginBottom = '4px';
					logoEmoji.style.filter = 'drop-shadow(0 2px 4px rgba(0, 0, 0, 0.3))';
					loadingBox.appendChild(logoEmoji);

					// Add text
					const loadingText = document.createElement('div');
					loadingText.style.color = 'white';
					loadingText.style.fontSize = '12px';
					loadingText.style.fontFamily = 'Consolas, Monaco, monospace';
					loadingText.style.fontWeight = '600';
					loadingText.style.letterSpacing = '1px';
					loadingText.style.textShadow = '0 1px 3px rgba(0, 0, 0, 0.5)';
					loadingText.innerText = `AGENT ${{browser_session_label}}`;
					loadingBox.appendChild(loadingText);

					loadingOverlay.appendChild(loadingBox);
					document.body.appendChild(loadingOverlay);

					// DVD screensaver animation variables
					let x = Math.random() * (window.innerWidth - 160);
					let y = Math.random() * (window.innerHeight - 80);
					let xSpeed = (Math.random() > 0.5 ? 1 : -1) * (1 + Math.random() * 0.5);
					let ySpeed = (Math.random() > 0.5 ? 1 : -1) * (1 + Math.random() * 0.5);
					let hue = 0;

					// Color palette for the DVD logo
					const colors = [
						'#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7',
						'#D63031', '#74B9FF', '#A29BFE', '#6C5CE7', '#FD79A8',
						'#FDCB6E', '#E17055', '#00B894', '#00CEC9', '#0984E3'
					];
					let currentColorIndex = 0;

					function changeColor() {{
						currentColorIndex = (currentColorIndex + 1) % colors.length;
						const newColor = colors[currentColorIndex];
						loadingBox.style.backgroundColor = newColor;
						loadingBox.style.boxShadow = `0 0 30px ${{newColor}}88, inset 0 0 20px rgba(255, 255, 255, 0.2)`;
					}}

					function animate() {{
						// Update position
						x += xSpeed * 2;
						y += ySpeed * 2;

						// Bounce off walls
						if (x <= 0 || x >= window.innerWidth - 160) {{
							xSpeed = -xSpeed;
							changeColor();
							x = Math.max(0, Math.min(x, window.innerWidth - 160));
						}}
						if (y <= 0 || y >= window.innerHeight - 80) {{
							ySpeed = -ySpeed;
							changeColor();
							y = Math.max(0, Math.min(y, window.innerHeight - 80));
						}}

						// Apply position
						loadingBox.style.left = x + 'px';
						loadingBox.style.top = y + 'px';

						requestAnimationFrame(animate);
					}}

					animate();
				}})('{browser_session_label}');
			"""
			
			await self.browser_session.cdp_client.send.Runtime.evaluate(
				params={'expression': script},
				session_id=session_id
			)
			
			# Detach from target
			await self.browser_session.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})
			
			# Dispatch event
			tab_index = await self.browser_session.get_tab_index(target_id)
			self.event_bus.dispatch(AboutBlankDVDScreensaverShownEvent(tab_index=tab_index))
			
		except Exception as e:
			self.logger.error(f'[AboutBlankWatchdog] Error injecting DVD screensaver: {e}')

