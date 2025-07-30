# Event-Driven Browser Architecture

## Overview

The refactored architecture uses the `bubus` EventBus system to create a decoupled, event-driven communication pattern between browser components.

## Architecture Layers

### 1. Agent/Controller Layer
- **Sends events**: `NavigateToUrlEvent`, `ClickElementEvent`, `InputTextEvent`, etc.
- **Receives events**: `BrowserStateResponse`, `ScreenshotResponse`, error events
- **Purpose**: High-level browser automation commands

### 2. BrowserSession Layer (session_eventbus.py)
- **Receives from above**: High-level browser commands
- **Sends to below**: Browser management events (`StartBrowserEvent`, `GetTabsInfoEvent`)
- **Receives from below**: Status updates (`BrowserStartedEvent`, `TabCreatedEvent`)
- **Purpose**: Manages browser lifecycle and routes commands to appropriate tabs

### 3. RemoteBrowserConnection Layer (remote.py)
- **Receives**: Browser management events
- **Sends**: Status updates and responses
- **Direct calls**: CDP and Playwright APIs (not through events)
- **Purpose**: Low-level browser control and CDP communication

## Event Flow Example

```python
# 1. Agent dispatches high-level command
agent_bus.dispatch(NavigateToUrlEvent(url="https://example.com"))
    ↓
# 2. BrowserSession handles it and forwards to connection
session._handle_navigate() → connection.navigate_page(tab_index=0, url=...)
    ↓
# 3. RemoteBrowserConnection executes via Playwright/CDP
page.goto(url) → NavigationCompleteEvent dispatched back up
```

## Benefits

1. **Decoupling**: Components communicate through events, not direct method calls
2. **Observability**: Any component can listen to events for monitoring/logging
3. **Extensibility**: New features can be added by listening to existing events
4. **Error Handling**: Errors propagate as events, allowing centralized handling
5. **Testing**: Components can be tested in isolation with mock event buses

## Key Events

### Control Events (Agent → Browser)
- `NavigateToUrlEvent` - Navigate to URL
- `ClickElementEvent` - Click element by index
- `InputTextEvent` - Type text into element
- `ScrollEvent` - Scroll page or element
- `TakeScreenshotEvent` - Capture screenshot

### Management Events (Session → Connection)
- `StartBrowserEvent` - Start/connect browser
- `StopBrowserEvent` - Stop/disconnect browser
- `GetTabsInfoEvent` - Get tab information
- `EvaluateJavaScriptEvent` - Run JS in page

### Status Events (Connection → Session)
- `BrowserStartedEvent` - Browser ready
- `BrowserStoppedEvent` - Browser stopped
- `TabCreatedEvent` - New tab opened
- `PageCrashedEvent` - Page crashed
- `NavigationCompleteEvent` - Navigation finished

### Response Events
- `BrowserStateResponse` - Current browser state
- `ScreenshotResponse` - Screenshot data
- `TabsInfoResponse` - Tab information
- `BrowserErrorEvent` - Error occurred

## Integration with Existing Code

To use this architecture, the agent/controller would:

```python
# Create event bus and session
event_bus = EventBus()
session = EventDrivenBrowserSession(
    browser_profile=profile,
    event_bus=event_bus,
)

# Register response handlers
event_bus.on(BrowserStateResponse, handle_state)
event_bus.on(BrowserErrorEvent, handle_error)

# Start browser
await session.start()

# Send commands via events
event_bus.dispatch(NavigateToUrlEvent(url="..."))
event_bus.dispatch(ClickElementEvent(index=5))
```

## Watchdog Services

The event-driven architecture makes it easy to add watchdog services:

```python
class CrashRecoveryWatchdog:
    def __init__(self, event_bus: EventBus):
        event_bus.on(PageCrashedEvent, self.handle_crash)
    
    async def handle_crash(self, event: PageCrashedEvent):
        # Attempt recovery
        logger.warning(f"Page crashed on tab {event.tab_index}")
        # Could dispatch recovery events here

class PerformanceMonitor:
    def __init__(self, event_bus: EventBus):
        event_bus.on(NavigationCompleteEvent, self.track_navigation)
    
    async def track_navigation(self, event: NavigationCompleteEvent):
        # Track page load times, etc.
        metrics.record_navigation(event.url, event.status)
```

## Migration Path

1. Keep existing `BrowserSession` as compatibility wrapper
2. Gradually migrate agents to use event-based API
3. Add new features using events without breaking existing code
4. Eventually deprecate direct method calls in favor of events
