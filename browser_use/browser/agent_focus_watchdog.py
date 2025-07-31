"""Agent focus watchdog for tracking which tab the agent is currently working on."""

import asyncio
from typing import Any

from bubus import EventBus
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict, PrivateAttr

from browser_use.browser.events import (
	AgentFocusChangedEvent,
	BrowserStartedEvent,
	BrowserStoppedEvent,
	CreateTabEvent,
	NavigationCompleteEvent,
	SwitchTabEvent,
	TabClosedEvent,
	TabCreatedEvent,
	TabsInfoRequestEvent,
	TabsInfoResponseEvent,
)
from browser_use.utils import logger


class AgentFocusWatchdog(BaseModel):
	"""Tracks which tab the agent is currently focused on."""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	event_bus: EventBus
	browser_session: Any  # Avoid circular import

	# Private state
	_current_agent_page: Page | None = PrivateAttr(default=None)
	_current_agent_tab_index: int = PrivateAttr(default=0)

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
		self.event_bus.on(SwitchTabEvent, self._handle_switch_tab)
		self.event_bus.on(NavigationCompleteEvent, self._handle_navigation_complete)

	async def _handle_browser_started(self, event: BrowserStartedEvent) -> None:
		"""Initialize agent focus when browser starts."""
		logger.info('[AgentFocusWatchdog] Browser started')
		await self._initialize_agent_focus()

	async def _handle_browser_stopped(self, event: BrowserStoppedEvent) -> None:
		"""Clear agent focus when browser stops."""
		logger.info('[AgentFocusWatchdog] Browser stopped')
		self._current_agent_page = None
		self._current_agent_tab_index = 0

	async def _handle_tab_created(self, event: TabCreatedEvent) -> None:
		"""Handle new tab creation - agent focus moves to new tab."""
		logger.debug(f'[AgentFocusWatchdog] Tab created: {event.url}')
		# When a new tab is created, agent typically focuses on it
		# Small delay to let tab finish loading
		await asyncio.sleep(0.5)
		await self._update_agent_focus_to_latest_tab()

	async def _handle_tab_closed(self, event: TabClosedEvent) -> None:
		"""Handle tab closure."""
		logger.debug(f'[AgentFocusWatchdog] Tab closed at index {event.tab_index}')

		# If the closed tab was the agent's current page, find a new one
		if self._current_agent_tab_index == event.tab_index:
			await self._find_new_agent_page()
		elif event.tab_index < self._current_agent_tab_index:
			# Adjust index if a tab before current was closed
			self._current_agent_tab_index -= 1

	async def _handle_switch_tab(self, event: SwitchTabEvent) -> None:
		"""Handle explicit tab switches by the agent."""
		logger.debug(f'[AgentFocusWatchdog] Agent switching to tab {event.tab_index}')
		await self._switch_agent_focus_to_tab(event.tab_index)

	async def _handle_navigation_complete(self, event: NavigationCompleteEvent) -> None:
		"""Update agent focus when navigation completes."""
		# Agent focus stays on the tab that navigated
		if event.tab_index != self._current_agent_tab_index:
			await self._switch_agent_focus_to_tab(event.tab_index)

	@property
	def current_agent_page(self) -> Page | None:
		"""Get the current page the agent is focused on."""
		return self._current_agent_page

	@property
	def current_agent_tab_index(self) -> int:
		"""Get the current tab index the agent is focused on."""
		return self._current_agent_tab_index

	async def _initialize_agent_focus(self) -> None:
		"""Initialize agent focus to first available page."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			return

		pages = self.browser_session._browser_context.pages
		if pages:
			self._current_agent_page = pages[0]
			self._current_agent_tab_index = 0
			self._dispatch_focus_changed()
			try:
				current_page = await self.browser_session.get_current_page()
				logger.info(f'[AgentFocusWatchdog] Initial agent focus set to tab 0: {current_page.url}')
			except ValueError:
				logger.info('[AgentFocusWatchdog] Initial agent focus set to tab 0')

	async def _update_agent_focus_to_latest_tab(self) -> None:
		"""Update agent focus to the latest (most recently created) tab."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			return

		pages = self.browser_session._browser_context.pages
		if pages:
			# Focus on the last tab (most recently created)
			self._current_agent_page = pages[-1]
			self._current_agent_tab_index = len(pages) - 1
			self._dispatch_focus_changed()
			try:
				current_page = await self.browser_session.get_current_page()
				logger.info(
					f'[AgentFocusWatchdog] Agent focus moved to new tab {self._current_agent_tab_index}: {current_page.url}'
				)
			except ValueError:
				logger.info(f'[AgentFocusWatchdog] Agent focus moved to new tab {self._current_agent_tab_index}')

	async def _switch_agent_focus_to_tab(self, tab_index: int) -> None:
		"""Switch agent focus to a specific tab index."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			return

		pages = self.browser_session._browser_context.pages
		if 0 <= tab_index < len(pages):
			self._current_agent_page = pages[tab_index]
			self._current_agent_tab_index = tab_index
			self._dispatch_focus_changed()
			try:
				current_page = await self.browser_session.get_current_page()
				logger.info(f'[AgentFocusWatchdog] Agent focus switched to tab {tab_index}: {current_page.url}')
			except ValueError:
				logger.info(f'[AgentFocusWatchdog] Agent focus switched to tab {tab_index}')

	async def _find_new_agent_page(self) -> None:
		"""Find a new page for agent focus when current one is closed."""
		if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
			self._current_agent_page = None
			self._current_agent_tab_index = 0
			return

		pages = self.browser_session._browser_context.pages
		if pages:
			# Try to stay at the same index, or go to the previous one
			new_index = min(self._current_agent_tab_index, len(pages) - 1)
			self._current_agent_page = pages[new_index]
			self._current_agent_tab_index = new_index
			self._dispatch_focus_changed()
			try:
				current_page = await self.browser_session.get_current_page()
				logger.info(f'[AgentFocusWatchdog] Agent focus moved to tab {new_index}: {current_page.url}')
			except ValueError:
				logger.info(f'[AgentFocusWatchdog] Agent focus moved to tab {new_index}')
		else:
			self._current_agent_page = None
			self._current_agent_tab_index = 0

	def _dispatch_focus_changed(self) -> None:
		"""Dispatch event when agent focus changes."""
		if self._current_agent_page:
			try:
				self.event_bus.dispatch(
					AgentFocusChangedEvent(
						tab_index=self._current_agent_tab_index,
						url=self._current_agent_page.url,
					)
				)
			except Exception as e:
				logger.error(f'[AgentFocusWatchdog] Error dispatching focus change: {e}')

	async def get_or_create_page(self) -> Page:
		"""Get current agent page or create a new one if none exists."""
		if self._current_agent_page and not self._current_agent_page.is_closed():
			return self._current_agent_page

		# No current page, request tabs info to find one
		self.event_bus.dispatch(TabsInfoRequestEvent())
		try:
			event_result = await self.event_bus.expect(TabsInfoResponseEvent, timeout=5.0)
			response: TabsInfoResponseEvent = event_result  # type: ignore
			if response.tabs:
				# Use the first available tab
				await self._switch_agent_focus_to_tab(0)
				if self._current_agent_page:
					return self._current_agent_page
		except TimeoutError:
			pass

		# No tabs available, create a new one
		logger.info('[AgentFocusWatchdog] No active page, creating new tab')
		self.event_bus.dispatch(CreateTabEvent(url='about:blank'))
		await asyncio.sleep(0.5)  # Wait for tab creation

		# Try to get the new page
		if hasattr(self.browser_session, '_browser_context') and self.browser_session._browser_context:
			pages = self.browser_session._browser_context.pages
			if pages:
				self._current_agent_page = pages[-1]
				self._current_agent_tab_index = len(pages) - 1
				self._dispatch_focus_changed()
				if self._current_agent_page:
					return self._current_agent_page

		raise ValueError('Failed to create or find an active page')
