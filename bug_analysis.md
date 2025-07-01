# Bug Analysis: False "about:blank" Tab Reports

## Issue Description

The browser-use system sometimes incorrectly reports tabs as `Tab 0: about:blank - ignore this tab and do not use` in the LLM context, even when there are actual websites loaded in those tabs.

## Root Cause

The issue is located in the `get_tabs_info()` method in `browser_use/browser/session.py` at lines 1823-1840:

```python
@require_initialization
@time_execution_async('--get_tabs_info')
async def get_tabs_info(self) -> list[TabInfo]:
    """Get information about all tabs"""
    assert self.browser_context is not None, 'BrowserContext is not set up'
    tabs_info = []
    for page_id, page in enumerate(self.browser_context.pages):
        try:
            title = await self._get_page_title(page)
            tab_info = TabInfo(page_id=page_id, url=page.url, title=title)
        except Exception:
            # page.title() can hang forever on tabs that are crashed/disappeared/about:blank
            # we dont want to try automating those tabs because they will hang the whole script
            self.logger.debug(f'⚠️ Failed to get tab info for tab #{page_id}: {_log_pretty_url(page.url)} (ignoring)')
            tab_info = TabInfo(page_id=page_id, url='about:blank', title='ignore this tab and do not use it')
        tabs_info.append(tab_info)
    
    return tabs_info
```

### The Problem

When `self._get_page_title(page)` fails (which can happen for various legitimate reasons), the code incorrectly:

1. **Overwrites the real URL** with `'about:blank'` even though `page.url` contains the actual URL
2. **Sets a misleading title** `'ignore this tab and do not use it'`

### When `_get_page_title()` Can Fail

The `_get_page_title()` method has a 1-second timeout:

```python
@retry(timeout=1, retries=0)  # Single attempt with 1s timeout, no retries
async def _get_page_title(self, page: Page) -> str:
    """Get page title with timeout protection."""
    return await page.title()
```

This can fail when:
- Page is still loading and takes more than 1 second to get the title
- Page has JavaScript that modifies the title dynamically
- Network is slow
- Page is in a crashed/unresponsive state
- Page has complex rendering that delays title availability

### Impact

This bug causes:
1. **LLM confusion**: The LLM receives incorrect information about available tabs
2. **Lost navigation opportunities**: Real websites are marked as "ignore this tab"
3. **Inconsistent behavior**: The same website might sometimes appear correctly and sometimes as "about:blank"

## Example from Slack Context

In the reported case:
- User has a tab with "Laminar" website at `https://www.lmnr.ai/project/...`
- System reports: `Tab 0: about:blank - ignore this tab and do not use`
- The actual URL was available but got masked by the fallback logic

## Proposed Solution

Instead of masking the real URL when title retrieval fails, we should:

1. **Preserve the real URL** from `page.url`
2. **Use a descriptive fallback title** that indicates the title couldn't be retrieved
3. **Allow the LLM to still use the tab** since the URL is valid

This maintains accurate tab information while gracefully handling title retrieval failures.

## Fix Implementation

The fix should modify the exception handling in `get_tabs_info()` to preserve the actual URL and use a more descriptive fallback title that doesn't mislead the LLM into ignoring valid tabs.