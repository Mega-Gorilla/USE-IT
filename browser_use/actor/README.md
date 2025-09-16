# Browser Actor

Browser Actor is a web automation library built directly on CDP.

## Usage

```python
from cdp_use import CDPClient
from browser_use.actor import Browser, Target, Element, Mouse

# Create client and browser
client = CDPClient(ws_url)
browser = Browser(client)
```

```python
# Get targets (multiple ways)
target = await browser.newTarget()  # Create blank tab
target = await browser.newTarget("https://example.com")  # Create tab with URL
targets = await browser.getTargets()  # Get all existing tabs

# Navigate target to URL
await target.goto("https://example.com")

await browser.closeTarget(target)
```

```python
# Find elements by CSS selector
elements = await target.getElementsByCSSSelector("input[type='text']")
buttons = await target.getElementsByCSSSelector("button.submit")

# Get element by backend node ID
element = await target.getElement(backend_node_id=12345)
```

Unlike other libraries, the native implementation for `getElementsByCSSSelector` does not support waiting for the element to be visible.

```python
# Element actions
await element.click(button='left', click_count=1, modifiers=['Control'])
await element.fill("Hello World") 
await element.hover()
await element.focus()
await element.check() 
await element.selectOption(["option1", "option2"])

# Element properties  
value = await element.getAttribute("value")
box = await element.getBoundingBox()
info = await element.getBasicInfo()
```

```python
# Mouse operations
mouse = await target.mouse
await mouse.click(x=100, y=200, button='left')
await mouse.move(x=300, y=400)
```

```python
# Target operations
mouse = await target.mouse
await mouse.scroll(x=0, y=100, delta_y=-500) # x,y (coordinates to scroll on), delta_y (how much to scroll)
await target.press("Control+A")  # Key combinations supported
await target.press("Escape")
await target.setViewportSize(width=1920, height=1080)
await target.reload()
page_screenshot = await target.screenshot()  # JPEG by default
page_png = await target.screenshot(format="png")
```
Ø
## Core Classes

- **Browser**, **Target**, **Element**, **Mouse**: Core classes for browser operations

## API Reference

### Browser Methods
- `newTarget(url=None)` → `Target` - Create blank tab or navigate to URL
- `getTargets()` → `list[Target]` - Get all page/iframe targets
- `closeTarget(target: Target | str)` - Close target by object or ID
- `cookies(urls=None)` → `list[Cookie]` - Get cookies for specified URLs (or all if None)
- `clearCookies()` - Clear all cookies

### Target Methods
- `getElementsByCSSSelector(selector: str)` → `list[Element]` - Find elements by CSS selector
- `getElement(backend_node_id: int)` → `Element` - Get element by backend node ID
- `goto(url: str)` - Navigate this target to URL
- `goBack()`, `goForward()` - Navigate target history (with proper error handling)
- `evaluate(page_function: str, *args)` → `str` - Execute JavaScript (MUST use (...args) => format) and return string (objects/arrays are JSON-stringified)
- `press(key: str)` - Press key on page (supports "Control+A" format)
- `setViewportSize(width: int, height: int)` - Set viewport dimensions
- `reload()` - Reload the current page
- `screenshot(format='jpeg', quality=None)` → `str` - Take page screenshot and return base64
- `getUrl()` → `str`, `getTitle()` → `str` - Get page info

### Element Methods (Supported Only)
- `click(button='left', click_count=1, modifiers=None)` - Click element
- `fill(text: str)` - Fill input with text (clears first)
- `hover()` - Hover over element
- `focus()` - Focus the element
- `check()` - Toggle checkbox/radio button (clicks to change state)
- `selectOption(values: str | list[str])` - Select dropdown options (string or array)
- `dragTo(target: Element | Position, source_position=None, target_position=None)` - Drag to target
- `getAttribute(name: str)` → `str | None` - Get attribute value
- `getBoundingBox()` → `BoundingBox | None` - Get element position/size
- `screenshot(format='jpeg', quality=None)` → `str` - Take element screenshot and return base64
- `getBasicInfo()` → `ElementInfo` - Get comprehensive element information


### Mouse Methods
- `click(x: int, y: int, button='left', click_count=1)` - Click at coordinates
- `move(x: int, y: int, steps=1)` - Move to coordinates
- `down(button='left')`, `up(button='left')` - Press/release button
- `scroll(x=0, y=0, delta_x=None, delta_y=None)` - Scroll page (x,y coordinates to scroll on, delta_x/delta_y how much to scroll)

## Type Definitions

### Position
```python
class Position(TypedDict):
    x: float
    y: float
```

### BoundingBox
```python
class BoundingBox(TypedDict):
    x: float
    y: float
    width: float
    height: float
```

### ElementInfo
```python
class ElementInfo(TypedDict):
    backendNodeId: int
    nodeId: int | None
    nodeName: str
    nodeType: int
    nodeValue: str | None
    attributes: dict[str, str]
    boundingBox: BoundingBox | None
```

## Important LLM Usage Notes

**This is NOT Playwright.**. You can NOT use other methods than the ones described here. Key constraints for code generation:

**CRITICAL JAVASCRIPT EVALUATION RULES:**
- `target.evaluate()` MUST use (...args) => format and always returns string (objects become JSON strings)
- **STRING QUOTES**: Always use `target.evaluate('...')` (single quotes outside, double inside for CSS)
- **CSS SELECTORS**: Use `"input[name=\\"email\\"]"` format inside evaluate calls
- **ESCAPING**: Use `\\"` to escape double quotes inside selectors, never mix quote patterns

**METHOD RESTRICTIONS:**
- `getElementsByCSSSelector()` returns immediately, no waiting
- For dropdowns: use `element.selectOption("value")` or `element.selectOption(["val1", "val2"])`, not `element.fill()`
- No methods: `element.submit()`, `element.dispatchEvent()`, `element.getProperty()`, `target.querySelectorAll()`
- Form submission: click submit button or use `target.press("Enter")`
- Get properties: use `target.evaluate("() => element.value")` not `element.getProperty()`

**ERROR PREVENTION:**
- Loop prevention: verify page state changes with `target.getUrl()`, `target.getTitle()`, `element.getAttribute()`
- Validate selectors before use: ensure no excessive escaping like `\\\\\\\\`
- Test complex selectors: if a selector fails, simplify it step by step
