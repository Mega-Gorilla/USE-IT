# Browser Actor

Browser Actor is a web automation library built directly on CDP.

## Usage

### Option 1: Direct CDP Usage
```python
from cdp_use import CDPClient
from browser_use.actor import Target, Element, Mouse

# Create client directly - no Browser class needed
client = CDPClient(ws_url)
```

### Option 2: Integrated with Browser (Recommended)
```python
from browser_use import Browser

# Create and start browser session
browser = Browser()
await browser.start()

# Use integrated browser methods directly
target = await browser.new_target("https://example.com")
targets = await browser.get_targets()
current_target = await browser.get_current_target()
```

```python
# Get targets (multiple ways) - using browser methods
target = await browser.new_target()  # Create blank tab
target = await browser.new_target("https://example.com")  # Create tab with URL
targets = await browser.get_targets()  # Get all existing tabs

# Navigate target to URL
await target.goto("https://example.com")

await browser.close_target(target)
```

```python
# Find elements by CSS selector
elements = await target.get_elements_by_css_selector("input[type='text']")
buttons = await target.get_elements_by_css_selector("button.submit")

# Get element by backend node ID
element = await target.get_element(backend_node_id=12345)
```

Unlike other libraries, the native implementation for `get_elements_by_css_selector` does not support waiting for the element to be visible.

```python
# Element actions
await element.click(button='left', click_count=1, modifiers=['Control'])
await element.fill("Hello World") 
await element.hover()
await element.focus()
await element.check() 
await element.select_option(["option1", "option2"])

# Element properties  
value = await element.get_attribute("value")
box = await element.get_bounding_box()
info = await element.get_basic_info()
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
await target.set_viewport_size(width=1920, height=1080)
await target.reload()
page_screenshot = await target.screenshot()  # JPEG by default
page_png = await target.screenshot(format="png")
```

## Core Classes

- **Browser**: Main browser session with integrated browser methods
- **Target**, **Element**, **Mouse**: Core classes for browser operations

## API Reference

### Browser Methods (Browser Operations)
- `new_target(url=None)` → `Target` - Create blank tab or navigate to URL
- `get_targets()` → `list[Target]` - Get all page/iframe targets
- `close_target(target: Target | str)` - Close target by object or ID
- `cookies(urls=None)` → `list[Cookie]` - Get cookies for specified URLs (or all if None)
- `clear_cookies()` - Clear all cookies
- `get_current_target()` → `Target | None` - Get the current target

### Target Methods
- `get_elements_by_css_selector(selector: str)` → `list[Element]` - Find elements by CSS selector
- `get_element(backend_node_id: int)` → `Element` - Get element by backend node ID
- `goto(url: str)` - Navigate this target to URL
- `go_back()`, `go_forward()` - Navigate target history (with proper error handling)
- `evaluate(page_function: str, *args)` → `str` - Execute JavaScript (MUST use (...args) => format) and return string (objects/arrays are JSON-stringified)
- `press(key: str)` - Press key on page (supports "Control+A" format)
- `set_viewport_size(width: int, height: int)` - Set viewport dimensions
- `reload()` - Reload the current page
- `screenshot(format='jpeg', quality=None)` → `str` - Take page screenshot and return base64
- `get_url()` → `str`, `get_title()` → `str` - Get page info

### Element Methods (Supported Only)
- `click(button='left', click_count=1, modifiers=None)` - Click element
- `fill(text: str)` - Fill input with text (clears first)
- `hover()` - Hover over element
- `focus()` - Focus the element
- `check()` - Toggle checkbox/radio button (clicks to change state)
- `select_option(values: str | list[str])` - Select dropdown options (string or array)
- `drag_to(target: Element | Position, source_position=None, target_position=None)` - Drag to target
- `get_attribute(name: str)` → `str | None` - Get attribute value
- `get_bounding_box()` → `BoundingBox | None` - Get element position/size
- `screenshot(format='jpeg', quality=None)` → `str` - Take element screenshot and return base64
- `get_basic_info()` → `ElementInfo` - Get comprehensive element information


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
    backend_node_id: int
    node_id: int | None
    node_name: str
    node_type: int
    node_value: str | None
    attributes: dict[str, str]
    bounding_box: BoundingBox | None
```

## Important LLM Usage Notes

**This is NOT Playwright.**. You can NOT use other methods than the ones described here. Key constraints for code generation:

**CRITICAL JAVASCRIPT EVALUATION RULES:**
- `target.evaluate()` MUST use (...args) => format and always returns string (objects become JSON strings)
- **STRING QUOTES**: Always use `target.evaluate('...')` (single quotes outside, double inside for CSS)
- **CSS SELECTORS**: Use `"input[name=\\"email\\"]"` format inside evaluate calls
- **ESCAPING**: Use `\\"` to escape double quotes inside selectors, never mix quote patterns

**METHOD RESTRICTIONS:**
- `get_elements_by_css_selector()` returns immediately, no waiting
- For dropdowns: use `element.select_option("value")` or `element.select_option(["val1", "val2"])`, not `element.fill()`
- No methods: `element.submit()`, `element.dispatch_event()`, `element.get_property()`, `target.query_selector_all()`
- Form submission: click submit button or use `target.press("Enter")`
- Get properties: use `target.evaluate("() => element.value")` not `element.get_property()`

**ERROR PREVENTION:**
- Loop prevention: verify page state changes with `target.get_url()`, `target.get_title()`, `element.get_attribute()`
- Validate selectors before use: ensure no excessive escaping like `\\\\\\\\`
- Test complex selectors: if a selector fails, simplify it step by step
