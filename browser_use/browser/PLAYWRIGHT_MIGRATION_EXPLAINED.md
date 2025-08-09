# Browser Session Refactor: From Playwright to Pure CDP

## Overview
This PR represents a **massive architectural shift** from Playwright-based browser automation to pure Chrome DevTools Protocol (CDP) with an event-driven architecture.

## Key Architectural Changes

### Old Architecture (Playwright-based)
- **Library**: Used Playwright for browser automation
- **Style**: Imperative, direct method calls
- **State Management**: Instance variables and decorators
- **Example**: `await page.goto(url)`, `await page.click(selector)`
- **File**: `old_session.py` (4856 lines)

### New Architecture (Pure CDP + Event-driven)
- **Library**: Direct CDP communication via `cdp_use` library
- **Style**: Event-driven with EventBus pattern
- **State Management**: Watchdogs + Events
- **Example**: `event_bus.dispatch(NavigateToUrlEvent(url=url))`
- **File**: `session.py` (1606 lines, much cleaner!)

## Core Components

### 1. EventBus (`bubus` library)
Central event system for all communication:
```python
event_bus = EventBus()
event_bus.on(EventType, handler)
event_bus.dispatch(EventInstance)
```

### 2. Watchdogs Pattern
Modular components that listen to specific events and perform actions:
- **CrashWatchdog**: Monitors for browser crashes
- **DownloadsWatchdog**: Handles file downloads and PDF auto-downloads
- **SecurityWatchdog**: Manages security policies
- **StorageStateWatchdog**: Handles cookies and storage
- **LocalBrowserWatchdog**: Manages local browser lifecycle
- **AboutBlankWatchdog**: Handles about:blank navigation
- **DOMWatchdog**: Manages DOM state
- **ScreenshotWatchdog**: Handles screenshots
- **DefaultActionWatchdog**: Provides default behaviors

Each watchdog:
- Inherits from `BaseWatchdog`
- Declares what events it listens to (`LISTENS_TO`)
- Declares what events it emits (`EMITS`)
- Has handlers like `on_EventName()`

### 3. CDPSession
Manages CDP connections to browser targets (tabs):
```python
class CDPSession:
    cdp_client: CDPClient  # WebSocket connection to browser
    target_id: str         # Browser tab/target ID
    session_id: str        # CDP session ID
```

### 4. Event Types
Events flow through the system to trigger actions:
- **Lifecycle**: `BrowserStartEvent`, `BrowserStopEvent`, `BrowserStoppedEvent`
- **Navigation**: `NavigateToUrlEvent`, `NavigationStartedEvent`, `NavigationCompleteEvent`
- **Tabs**: `TabCreatedEvent`, `TabClosedEvent`, `SwitchTabEvent`
- **Downloads**: `FileDownloadedEvent`
- **State**: `BrowserStateRequestEvent`, `AgentFocusChangedEvent`
- **Errors**: `BrowserErrorEvent`, `BrowserCrashedEvent`

## PDF Auto-Download Feature Implementation

### What We Added:

1. **Configuration** (`profile.py`):
   ```python
   auto_download_pdfs: bool = Field(
       default=True,
       description='Automatically download PDFs when navigating to PDF viewer pages.'
   )
   ```

2. **Session-level Download Tracking** (`session.py`):
   ```python
   # Track files downloaded during this session
   _downloaded_files: list[str] = PrivateAttr(default_factory=list)
   
   # Listen to download events
   self.event_bus.on(FileDownloadedEvent, self.on_FileDownloadedEvent)
   
   async def on_FileDownloadedEvent(self, event: FileDownloadedEvent):
       if event.path and event.path not in self._downloaded_files:
           self._downloaded_files.append(event.path)
   ```

3. **Downloads Watchdog** (`downloads_watchdog.py`):
   - Checks for PDF viewers on navigation: `check_for_pdf_viewer()`
   - Auto-downloads PDFs if enabled: `trigger_pdf_download()`
   - Uses JavaScript fetch to download from browser cache
   - Dispatches `FileDownloadedEvent` when complete

4. **Agent Integration** (`agent/service.py`):
   - `_check_and_update_downloads()` - Updates available files each turn
   - Accesses `browser_session.downloaded_files` property
   - Makes downloaded files available to agent actions

## Event Flow Example

When navigating to a PDF:
1. User/Agent dispatches `NavigateToUrlEvent`
2. Browser navigates to URL
3. `NavigationCompleteEvent` is dispatched
4. `DownloadsWatchdog` receives event, checks if page is PDF viewer
5. If PDF and `auto_download_pdfs=True`, downloads the PDF
6. `FileDownloadedEvent` is dispatched
7. `BrowserSession` receives event, adds to `_downloaded_files` list
8. Agent's `_check_and_update_downloads()` picks up new file

## Benefits of New Architecture

1. **Modularity**: Each concern is isolated in its own watchdog
2. **Testability**: Events can be easily mocked and tested
3. **Extensibility**: New features = new watchdog + events
4. **Debugging**: Event flow can be traced and logged
5. **Async-first**: Better concurrency with event-driven design
6. **Smaller Core**: Session.py is 1/3 the size of old_session.py
7. **No Playwright Dependency**: Direct CDP = more control, less overhead

## Migration Notes

### Breaking Changes:
- No more Playwright `Page`, `Browser`, `BrowserContext` objects
- Methods like `page.goto()` replaced with events
- Decorators like `@require_healthy_browser` removed

### Compatibility Layer:
The new `BrowserSession` provides some backwards-compatible methods:
- `downloaded_files` property (though implementation changed)
- `get_browser_state_summary()` still works
- CDP-based replacements for common operations

### What's Still TODO:
- Some error handling edge cases
- Performance optimizations for event dispatching
- More comprehensive test coverage
- Documentation for watchdog development

## Testing

Run the test script to verify PDF auto-download:
```bash
python test_pdf_download.py
```

This tests:
1. PDF auto-download when enabled
2. No auto-download when disabled  
3. Downloaded files tracking in session
4. Agent's available_file_paths updates

## Conclusion

This refactor is a fundamental architectural change that makes browser-use more:
- **Maintainable**: Clear separation of concerns
- **Scalable**: Event-driven architecture scales better
- **Debuggable**: Event flow is traceable
- **Extensible**: Easy to add new features via watchdogs

The PDF auto-download feature was successfully ported to work within this new event-driven paradigm, demonstrating how features should be implemented going forward.