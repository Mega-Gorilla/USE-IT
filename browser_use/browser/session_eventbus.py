"""Event-driven browser session using bubus."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.events import (
    BrowserErrorEvent,
    BrowserStartedEvent,
    BrowserStateResponse,
    BrowserStoppedEvent,
    ClickElementEvent,
    CreateTabEvent,
    GetBrowserStateEvent,
    GetTabsInfoEvent,
    InputTextEvent,
    NavigateToUrlEvent,
    ScreenshotResponse,
    ScrollEvent,
    StartBrowserEvent,
    StopBrowserEvent,
    SwitchTabEvent,
    TabsInfoResponse,
    TakeScreenshotEvent,
)
from browser_use.browser.profile import BrowserProfile
from bubus import EventBus

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page


class EventDrivenBrowserSession(BaseModel):
    """Browser session that communicates via events.
    
    This session acts as a bridge between high-level commands from
    agents/controllers and low-level browser operations.
    """
    
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra='forbid',
    )
    
    # Core configuration
    browser_profile: BrowserProfile
    id: str = Field(default_factory=lambda: uuid7str())
    
    # Event bus for communication
    event_bus: EventBus
    
    # Connection info
    cdp_url: str | None = None
    
    # State
    _started: bool = PrivateAttr(default=False)
    _connection: Any = PrivateAttr(default=None)  # Will be RemoteBrowserConnection
    _current_tab_index: int = PrivateAttr(default=0)
    _tabs: list[dict[str, Any]] = PrivateAttr(default_factory=list)
    
    def __init__(self, **data):
        """Initialize session and register event handlers."""
        super().__init__(**data)
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register event handlers for incoming commands."""
        # Navigation and interaction
        self.event_bus.on(NavigateToUrlEvent, self._handle_navigate)
        self.event_bus.on(ClickElementEvent, self._handle_click)
        self.event_bus.on(InputTextEvent, self._handle_input_text)
        self.event_bus.on(ScrollEvent, self._handle_scroll)
        
        # Tab management
        self.event_bus.on(SwitchTabEvent, self._handle_switch_tab)
        self.event_bus.on(CreateTabEvent, self._handle_create_tab)
        
        # Browser state
        self.event_bus.on(GetBrowserStateEvent, self._handle_get_state)
        self.event_bus.on(TakeScreenshotEvent, self._handle_screenshot)
        
        # Connection events from browser
        self.event_bus.on(BrowserStartedEvent, self._handle_browser_started)
        self.event_bus.on(BrowserStoppedEvent, self._handle_browser_stopped)
    
    async def start(self) -> Self:
        """Start the browser session."""
        if self._started:
            return self
        
        # Create connection based on whether we have CDP URL
        if self.cdp_url:
            from browser_use.browser.remote import RemoteBrowserConnection
            self._connection = RemoteBrowserConnection(
                browser_profile=self.browser_profile,
                cdp_url=self.cdp_url,
                event_bus=self.event_bus,
            )
        else:
            from browser_use.browser.local import LocalBrowserConnection
            self._connection = LocalBrowserConnection(
                browser_profile=self.browser_profile,
                event_bus=self.event_bus,
            )
        
        # Dispatch start event and wait for response
        start_event = self.event_bus.dispatch(StartBrowserEvent(
            cdp_url=self.cdp_url,
            launch_options=self.browser_profile.model_dump(),
        ))
        
        # Wait for browser to start
        try:
            await asyncio.wait_for(start_event, timeout=30)
            self._started = True
        except asyncio.TimeoutError:
            raise RuntimeError("Browser failed to start within 30 seconds")
        
        return self
    
    async def stop(self) -> None:
        """Stop the browser session."""
        if not self._started:
            return
        
        stop_event = self.event_bus.dispatch(StopBrowserEvent())
        await stop_event
        self._started = False
    
    # Command handlers
    async def _handle_navigate(self, event: NavigateToUrlEvent) -> None:
        """Handle navigation request."""
        if not self._started:
            self.event_bus.dispatch(BrowserErrorEvent(
                error_type='NotStarted',
                message='Browser session not started',
            ))
            return
        
        # Forward to connection with current tab context
        await self._connection.navigate_page(
            tab_index=self._current_tab_index,
            url=event.url,
            wait_until=event.wait_until,
        )
    
    async def _handle_click(self, event: ClickElementEvent) -> None:
        """Handle click request."""
        if not self._started:
            self.event_bus.dispatch(BrowserErrorEvent(
                error_type='NotStarted',
                message='Browser session not started',
            ))
            return
        
        await self._connection.click_element(
            tab_index=self._current_tab_index,
            element_index=event.index,
            button=event.button,
            click_count=event.click_count,
        )
    
    async def _handle_input_text(self, event: InputTextEvent) -> None:
        """Handle text input request."""
        if not self._started:
            self.event_bus.dispatch(BrowserErrorEvent(
                error_type='NotStarted',
                message='Browser session not started',
            ))
            return
        
        await self._connection.input_text(
            tab_index=self._current_tab_index,
            element_index=event.index,
            text=event.text,
            clear_existing=event.clear_existing,
        )
    
    async def _handle_scroll(self, event: ScrollEvent) -> None:
        """Handle scroll request."""
        if not self._started:
            self.event_bus.dispatch(BrowserErrorEvent(
                error_type='NotStarted',
                message='Browser session not started',
            ))
            return
        
        await self._connection.scroll(
            tab_index=self._current_tab_index,
            direction=event.direction,
            amount=event.amount,
            element_index=event.element_index,
        )
    
    async def _handle_switch_tab(self, event: SwitchTabEvent) -> None:
        """Handle tab switch request."""
        if 0 <= event.tab_index < len(self._tabs):
            self._current_tab_index = event.tab_index
    
    async def _handle_create_tab(self, event: CreateTabEvent) -> None:
        """Handle new tab creation."""
        if not self._started:
            return
        
        await self._connection.create_tab(url=event.url)
    
    async def _handle_get_state(self, event: GetBrowserStateEvent) -> None:
        """Handle browser state request."""
        if not self._started:
            self.event_bus.dispatch(BrowserStateResponse(
                state={'error': 'Browser not started'},
            ))
            return
        
        # Get state from connection
        state = await self._connection.get_browser_state(
            tab_index=self._current_tab_index,
            include_dom=event.include_dom,
        )
        
        screenshot = None
        if event.include_screenshot:
            screenshot = await self._connection.take_screenshot(
                tab_index=self._current_tab_index,
            )
        
        self.event_bus.dispatch(BrowserStateResponse(
            state=state,
            screenshot=screenshot,
        ))
    
    async def _handle_screenshot(self, event: TakeScreenshotEvent) -> None:
        """Handle screenshot request."""
        if not self._started:
            return
        
        screenshot = await self._connection.take_screenshot(
            tab_index=self._current_tab_index,
            full_page=event.full_page,
            clip=event.clip,
        )
        
        self.event_bus.dispatch(ScreenshotResponse(screenshot=screenshot))
    
    # Status event handlers
    async def _handle_browser_started(self, event: BrowserStartedEvent) -> None:
        """Handle browser started notification."""
        self.cdp_url = event.cdp_url
        
        # Get initial tabs info
        tabs_event = self.event_bus.dispatch(GetTabsInfoEvent())
        response = await tabs_event.event_result()
        if isinstance(response, TabsInfoResponse):
            self._tabs = response.tabs
    
    async def _handle_browser_stopped(self, event: BrowserStoppedEvent) -> None:
        """Handle browser stopped notification."""
        self._tabs.clear()
        self._current_tab_index = 0


# Import uuid7str for ID generation
try:
    from uuid_extensions import uuid7str
except ImportError:
    import uuid
    def uuid7str() -> str:
        return str(uuid.uuid4())