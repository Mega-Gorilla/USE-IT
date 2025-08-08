"""Navigation watchdog for monitoring tab lifecycle and agent focus tracking."""

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent
from pydantic import PrivateAttr

from browser_use.browser.events import (
	AgentFocusChangedEvent,
	BrowserConnectedEvent,
	BrowserErrorEvent,
	BrowserStoppedEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	NavigationStartedEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog

if TYPE_CHECKING:
	pass


class NavigationWatchdog(BaseWatchdog):
	"""Monitors tab creation, lifecycle events, and tracks agent focus."""

	# Event contracts
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [
		BrowserConnectedEvent,
		BrowserStoppedEvent,
		TabCreatedEvent,
		TabClosedEvent,
		SwitchTabEvent,
		NavigateToUrlEvent,
		NavigationCompleteEvent,
	]
	EMITS: ClassVar[list[type[BaseEvent]]] = [
		AgentFocusChangedEvent,
		BrowserErrorEvent,
		TabClosedEvent,
		NavigationStartedEvent,
		NavigationCompleteEvent,
	]

	# Track target close handlers
	_target_close_handlers: dict[str, Any] = PrivateAttr(default_factory=dict)

	async def attach_to_target(self, target_id: str) -> None:
		"""Set up monitoring for a target - tracks target lifecycle."""
		# CDP doesn't have direct target close events, we'll handle via Target.targetDestroyed
		self._target_close_handlers[target_id] = True

	# ========== Browser Lifecycle Events ==========

	async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
		"""Initialize agent focus when browser is connected."""
		self.logger.debug('[NavigationWatchdog] Browser connected, initializing agent focus')
		await self._initialize_agent_focus()

	async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
		"""Clear agent focus when browser stops."""
		# Clear target handlers
		self._target_close_handlers.clear()
		if self.browser_session.cdp_session:
			self.browser_session.cdp_session.target_id = None  # type: ignore

	# ========== Tab Lifecycle Events ==========

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		"""Handle new tab creation - track target and possibly move agent focus."""
		# self.logger.debug(f'[NavigationWatchdog] Tab created: {event.url}')

		# Get all targets and track the new one
		try:
			targets = await self.browser_session._cdp_get_all_pages()
			if event.tab_index < len(targets):
				target_id = targets[event.tab_index]['targetId']
				self._track_target(target_id)
		except Exception as e:
			self.logger.error(f'[NavigationWatchdog] Error tracking new target: {e}')

		# When a new tab is created, agent typically focuses on it immediately
		await self._update_agent_focus_to_latest_tab()

	async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
		"""Handle tab closure."""
		self.logger.debug(f'[NavigationWatchdog] Tab closed at index {event.tab_index}')

		# If the closed tab was the agent's current target, find a new one
		current_tab_index = await self._get_current_tab_index()
		if current_tab_index == event.tab_index:
			await self._find_new_agent_target()
		# No need to adjust tab_index since it's computed from target position

	async def on_SwitchTabEvent(self, event: SwitchTabEvent) -> None:
		"""Handle explicit tab switches by the agent."""
		self.logger.debug(f'[NavigationWatchdog] Agent switching to tab {event.tab_index}')
		await self._switch_agent_focus_to_tab(event.tab_index)

	async def on_NavigateToUrlEvent(self, event: NavigateToUrlEvent) -> None:
		"""Handle all navigation requests with security enforcement and complete logic."""

		# Check if browser session is still available
		if not self.browser_session:
			self.logger.debug('[NavigationWatchdog] No browser session available, ignoring navigation')
			return

		# SECURITY CHECK: Block disallowed URLs before navigation starts
		if not self._is_url_allowed(event.url):
			self.logger.warning(f'⛔️ Blocking navigation to disallowed URL: {event.url}')
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NavigationBlocked',
					message=f'Navigation blocked to disallowed URL: {event.url}',
					details={'url': event.url, 'reason': 'not_in_allowed_domains'},
				)
			)
			return

		try:
			# Handle new tab navigation
			if event.new_tab:
				# Create new tab using CDP
				target_id = await self.browser_session._cdp_create_new_page(event.url or 'about:blank')
				self._track_target(target_id)

				# Get tab index for events (after adding the target)
				targets = await self.browser_session._cdp_get_all_pages()
				tab_index = len(targets) - 1

				# Navigate to URL if provided (already done in create_new_page if URL was provided)
				if event.url:
					# Dispatch navigation started event
					from browser_use.browser.events import NavigationStartedEvent

					self.event_bus.dispatch(
						NavigationStartedEvent(
							tab_index=tab_index,
							url=event.url,
						)
					)

					try:
						# Navigation already happened in _cdp_create_new_page
						# Just dispatch completion
						response_status = None  # CDP doesn't return response status directly

						# Dispatch completion event
						self.event_bus.dispatch(
							NavigationCompleteEvent(
								tab_index=tab_index,
								url=event.url,
								status=response_status,
							)
						)

						# Network monitoring is handled by CrashWatchdog

					except TimeoutError:
						# Handle navigation timeout for new tab
						error_message = 'Navigation timed out after 30 seconds'
						loading_status = 'Navigation timeout: 30 seconds'
						self.logger.warning(f'[NavigationWatchdog] Navigation to {event.url} timed out in new tab')

						self.event_bus.dispatch(
							NavigationCompleteEvent(
								tab_index=tab_index,
								url=event.url,
								status=None,
								error_message=error_message,
								loading_status=loading_status,
							)
						)
					except Exception as e:
						# Handle other navigation errors for new tab
						error_message = str(e)
						loading_status = None
						if 'timeout' in error_message.lower() or 'timed out' in error_message.lower():
							loading_status = f'Navigation timeout: {error_message}'

						self.event_bus.dispatch(
							NavigationCompleteEvent(
								tab_index=tab_index,
								url=event.url,
								status=None,
								error_message=error_message,
								loading_status=loading_status,
							)
						)

				# Update agent focus to the new tab immediately
				assert self.browser_session.cdp_session is not None, 'No current target ID'
				self.browser_session.cdp_session.target_id = target_id
				await self._dispatch_focus_changed()

				# ONLY dispatch TabCreatedEvent for NEW tabs (not existing tab navigation)
				from browser_use.browser.events import TabCreatedEvent

				# Get the actual URL from the target
				target_url = await self._get_target_url(target_id)
				self.event_bus.dispatch(
					TabCreatedEvent(
						tab_index=tab_index,
						url=target_url or event.url,
					)
				)
				return

			# Handle timeout navigation
			if event.timeout_ms is not None:
				# Create a new navigation event without timeout for the actual navigation
				nav_event = self.event_bus.dispatch(NavigateToUrlEvent(url=event.url, wait_until=event.wait_until))
				try:
					await asyncio.wait_for(nav_event, timeout=event.timeout_ms / 1000.0)
				except TimeoutError:
					self.logger.warning(f'Navigation to {event.url} timed out after {event.timeout_ms}ms')
					# Dispatch NavigationCompleteEvent for timeout error
					self.event_bus.dispatch(
						NavigationCompleteEvent(
							tab_index=0,  # Default tab index since we don't have access to the actual target
							url=event.url,
							status=None,
							error_message=f'Navigation timed out after {event.timeout_ms}ms',
							loading_status=f'Navigation timeout after {event.timeout_ms}ms',
						)
					)
				return

			# Handle standard navigation
			# Get the target to navigate - use current agent target or create one
			target_id = None
			try:
				target_id = await self.get_or_create_target()
			except Exception as e:
				self.logger.warning(f'[NavigationWatchdog] get_or_create_target failed: {e}')
				# Fallback to first target
				try:
					targets = await self.browser_session._cdp_get_all_pages()
					if targets:
						target_id = targets[0]['targetId']
				except Exception as e2:
					self.logger.error(f'[NavigationWatchdog] Failed to get any targets: {e2}')
					raise

			if not target_id:
				raise ValueError('Failed to get or create a target for navigation')

			# Get tab index
			tab_index = await self._get_tab_index_for_target(target_id)

			# Dispatch navigation started event
			from browser_use.browser.events import NavigationStartedEvent

			self.event_bus.dispatch(
				NavigationStartedEvent(
					tab_index=tab_index,
					url=event.url,
				)
			)

			# Perform navigation with timeout protection using CDP
			await asyncio.wait_for(
				self.browser_session._cdp_navigate(event.url, target_id),
				timeout=30.0,  # 30 second timeout to prevent hanging
			)
			response_status = None  # CDP doesn't return response status directly

			# Dispatch completion event
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=tab_index,
					url=event.url,
					status=response_status,
				)
			)

			# Network monitoring is handled by CrashWatchdog

		except TimeoutError:
			# Handle navigation timeout for standard navigation
			error_message = 'Navigation timed out after 30 seconds'
			loading_status = 'Navigation timeout: 30 seconds'
			self.logger.warning(f'[NavigationWatchdog] Navigation to {event.url} timed out in standard navigation')

			# Get tab index for timeout error reporting
			tab_index = await self._get_tab_index_for_target(target_id) if target_id else 0

			# Dispatch NavigationCompleteEvent with timeout error details
			self.event_bus.dispatch(
				NavigationCompleteEvent(
					tab_index=tab_index,
					url=event.url,
					status=None,
					error_message=error_message,
					loading_status=loading_status,
				)
			)
		except Exception as e:
			# Handle other navigation errors
			error_message = str(e)
			loading_status = None

			# Check for timeout errors
			if 'timeout' in error_message.lower() or 'timed out' in error_message.lower():
				loading_status = f'Navigation timeout: {error_message}'

			# Get tab index for error reporting
			tab_index = 0  # Default to first tab for errors

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

	async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
		"""Update agent focus when navigation completes and enforce security."""
		# Check if the navigated URL is allowed
		if not self._is_url_allowed(event.url):
			self.logger.warning(f'⛔️ Navigation to non-allowed URL detected: {event.url}')
			# Dispatch browser error
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NavigationBlocked',
					message=f'Navigation to non-allowed URL: {event.url}',
					details={'url': event.url, 'tab_index': event.tab_index},
				)
			)
			# Close the target that navigated to the disallowed URL
			try:
				targets = await self.browser_session._cdp_get_all_pages()
				if 0 <= event.tab_index < len(targets):
					target_id = targets[event.tab_index]['targetId']
					await self.browser_session._cdp_close_page(target_id)
					self.logger.info(f'⛔️ Closed target with non-allowed URL: {event.url}')
			except Exception as e:
				self.logger.error(f'⛔️ Failed to close target with non-allowed URL: {str(e)}')
			return

		# Agent focus stays on the tab that navigated
		current_tab_index = await self._get_current_tab_index()
		if event.tab_index != current_tab_index:
			await self._switch_agent_focus_to_tab(event.tab_index)

	# ========== Tab Tracking Methods ==========

	def _track_target(self, target_id: str) -> None:
		"""Track a target for lifecycle events."""
		# self.logger.debug(f'[NavigationWatchdog] Started tracking target: {target_id}')
		self._target_close_handlers[target_id] = True

	def _handle_target_close(self, target_id: str) -> None:
		"""Handle target close event."""
		try:
			# Try to get tab index before target is fully closed
			tab_index = asyncio.run(self._get_tab_index_for_target(target_id))
			if tab_index == -1:
				tab_index = 0  # Fallback if target already removed

			# Emit TabClosedEvent
			self.event_bus.dispatch(TabClosedEvent(tab_index=tab_index))
			self.logger.info(f'[NavigationWatchdog] Target closed, emitted TabClosedEvent for tab {tab_index}')

			# Clean up the handler from our tracking dict
			self._target_close_handlers.pop(target_id, None)

		except Exception as e:
			self.logger.error(f'[NavigationWatchdog] Error handling target close: {e}')

	# ========== Security Methods ==========

	def _is_url_allowed(self, url: str) -> bool:
		"""Check if a URL is allowed based on the allowed_domains configuration.

		Args:
			url: The URL to check

		Returns:
			True if the URL is allowed, False otherwise
		"""
		# If no allowed_domains specified, allow all URLs
		if not self.browser_session.browser_profile.allowed_domains:
			return True

		# Always allow internal browser targets
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
		for pattern in self.browser_session.browser_profile.allowed_domains:
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

	# ========== Agent Focus Tracking Methods ==========

	@property
	def current_agent_target(self) -> str | None:
		"""Get the current target ID the agent is focused on."""
		return self.browser_session.cdp_session.target_id if self.browser_session.cdp_session else None

	@property
	def current_agent_tab_index(self) -> int:
		"""Get the current tab index the agent is focused on."""
		return asyncio.run(self._get_current_tab_index())

	@property
	def tab_index(self) -> int:
		"""Get the current tab index derived from target position."""
		return asyncio.run(self._get_current_tab_index())

	async def _get_current_tab_index(self) -> int:
		"""Get the current tab index from target position."""
		if self.browser_session.cdp_session is None or self.browser_session.cdp_session.target_id is None:
			return 0
		return await self._get_tab_index_for_target(self.browser_session.cdp_session.target_id)

	async def _get_tab_index_for_target(self, target_id: str) -> int:
		"""Get tab index for a specific target ID."""
		try:
			targets = await self.browser_session._cdp_get_all_pages()
			for i, target in enumerate(targets):
				if target['targetId'] == target_id:
					return i
		except Exception:
			pass
		return -1

	async def _get_target_url(self, target_id: str) -> str | None:
		"""Get the URL for a specific target ID."""
		try:
			targets = await self.browser_session._cdp_get_all_pages()
			for target in targets:
				if target['targetId'] == target_id:
					return target.get('url', '')
		except Exception:
			pass
		return None

	async def _initialize_agent_focus(self) -> None:
		"""Initialize agent focus to first available target and dispatch TabCreatedEvent for initial pages."""
		# Get all targets and filter for valid page/tab targets
		all_targets = await self.browser_session.cdp_client.send.Target.getTargets()
		targets = [
			t
			for t in all_targets.get('targetInfos', [])
			if self.browser_session._is_valid_target(t) and t.get('type') in ('page', 'tab')
		]

		if targets:
			# Set initial focus to first target BEFORE dispatching events
			await self.browser_session.attach_cdp_session(targets[0]['targetId'])

			# Dispatch TabCreatedEvent for all initial pages so watchdogs can process them
			from browser_use.browser.events import TabCreatedEvent

			for i, target in enumerate(targets):
				target_url = target.get('url', '')
				self.logger.debug(f'[NavigationWatchdog] Dispatching TabCreatedEvent for initial tab {i}: {target_url}')
				self.event_bus.dispatch(TabCreatedEvent(tab_index=i, url=target_url))

			# Dispatch focus changed event
			await self._dispatch_focus_changed()
			target_url = self.browser_session.cdp_session.url if self.browser_session.cdp_session else ''
			self.logger.info(f'[NavigationWatchdog] Initial agent focus set to tab 0: {target_url}')

	async def _update_agent_focus_to_latest_tab(self) -> None:
		"""Update agent focus to the latest (most recently created) tab."""
		targets = await self.browser_session._cdp_get_all_pages()
		if targets:
			# Focus on the last target (most recently created)
			if self.browser_session.cdp_session:
				self.browser_session.cdp_session.target_id = targets[-1]['targetId']
			await self._dispatch_focus_changed()
			target_url = await self._get_target_url(
				self.browser_session.cdp_session.target_id if self.browser_session.cdp_session else None
			)
			tab_index = await self._get_current_tab_index()
			self.logger.info(f'[NavigationWatchdog] 👀 Agent focus moved to new tab {tab_index}: {target_url}')

	async def _switch_agent_focus_to_tab(self, tab_index: int) -> None:
		"""Switch agent focus to a specific tab index."""
		targets = await self.browser_session._cdp_get_all_pages()
		if 0 <= tab_index < len(targets):
			if self.browser_session.cdp_session:
				self.browser_session.cdp_session.target_id = targets[tab_index]['targetId']
			await self._dispatch_focus_changed()
			target_url = await self._get_target_url(
				self.browser_session.cdp_session.target_id if self.browser_session.cdp_session else None
			)
			self.logger.info(f'[NavigationWatchdog] Agent focus switched to tab {tab_index}: {target_url}')

	async def _find_new_agent_target(self) -> None:
		"""Find a new target for agent focus when current one is closed."""
		targets = await self.browser_session._cdp_get_all_pages()
		if targets:
			# Try to stay at the same index, or go to the previous one
			current_index = await self._get_current_tab_index()
			new_index = min(current_index, len(targets) - 1)
			if self.browser_session.cdp_session:
				self.browser_session.cdp_session.target_id = targets[new_index]['targetId']
			await self._dispatch_focus_changed()
			target_url = await self._get_target_url(self.browser_session.cdp_session.target_id if self.browser_session.cdp_session else None)
			self.logger.info(f'[NavigationWatchdog] Agent focus moved to tab {new_index}: {target_url}')
		else:
			if self.browser_session:
				self.browser_session.cdp_session.target_id = None

	async def _dispatch_focus_changed(self) -> None:
		"""Dispatch event when agent focus changes."""
		if self.browser_session.cdp_session.target_id:
			try:
				# Get URL asynchronously
				target_url = await self._get_target_url(self.browser_session.cdp_session.target_id)
				tab_index = await self._get_current_tab_index()
				self.event_bus.dispatch(
					AgentFocusChangedEvent(
						tab_index=tab_index,
						url=target_url or '',
					)
				)
			except Exception as e:
				self.logger.error(f'[NavigationWatchdog] Error dispatching focus change: {e}')

	async def get_or_create_target(self) -> str:
		"""Get current agent target or create a new one if none exists."""
		if self.browser_session.cdp_session.target_id:
			# Check if current target URL is still allowed
			target_url = await self._get_target_url(self.browser_session.cdp_session.target_id)
			if target_url and not self._is_url_allowed(target_url):
				self.logger.warning(f'⛔️ Current target URL no longer allowed: {target_url}')
				# Close the current target and find/create a new one
				try:
					await self.browser_session._cdp_close_page(self.browser_session.cdp_session.target_id)
				except Exception:
					pass
				self.browser_session.cdp_session.target_id = None
			else:
				return self.browser_session.cdp_session.target_id

		try:
			targets = await self.browser_session._cdp_get_all_pages()
			for target in targets:
				target_url = target.get('url', '')
				if self._is_url_allowed(target_url):
					self.browser_session.cdp_session.target_id = target['targetId']
					await self._dispatch_focus_changed()
					return self.browser_session.cdp_session.target_id
		except Exception as e:
			self.logger.warning(f'[NavigationWatchdog] Failed to get targets: {e}')
			# Don't pass TimeoutError, re-raise other exceptions
			if not isinstance(e, TimeoutError):
				raise

		# No tabs available or no allowed tabs, create a new one with about:blank
		self.logger.info('[NavigationWatchdog] No active target, creating new tab')

		nav_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
		await nav_event  # Wait for navigation to complete

		# Try to get the new target
		targets = await self.browser_session._cdp_get_all_pages()
		if targets:
			self.browser_session.cdp_session.target_id = targets[-1]['targetId']
			await self._dispatch_focus_changed()
			return self.browser_session.cdp_session.target_id

		raise ValueError('Failed to create or find an active target')
