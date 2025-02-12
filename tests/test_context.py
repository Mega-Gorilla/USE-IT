import pytest
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.views import DOMElementNode
from browser_use.browser.views import BrowserState, BrowserError
import asyncio
import base64
import json
import os

def test_convert_simple_xpath_to_css_selector():
    """
    Test that BrowserContext._convert_simple_xpath_to_css_selector correctly transforms
    basic XPath expressions into CSS selectors.
    """
    # Test with a numeric index
    xpath1 = '/html/body/div[2]/span'
    expected1 = 'html > body > div:nth-of-type(2) > span'
    result1 = BrowserContext._convert_simple_xpath_to_css_selector(xpath1)
    assert result1 == expected1, f"Expected '{expected1}', got '{result1}'"
    # Test with an empty xpath
    xpath2 = ''
    expected2 = ''
    result2 = BrowserContext._convert_simple_xpath_to_css_selector(xpath2)
    assert result2 == expected2, f"Expected empty string, got '{result2}'"
    # Test with a different numeric index in a list item
    xpath3 = '/ul/li[3]/a'
    expected3 = 'ul > li:nth-of-type(3) > a'
    result3 = BrowserContext._convert_simple_xpath_to_css_selector(xpath3)
    assert result3 == expected3, f"Expected '{expected3}', got '{result3}'"
    # Test with the last() function indicating last element of type
    xpath4 = '/table/tr[last()]/td'
    expected4 = 'table > tr:last-of-type > td'
    result4 = BrowserContext._convert_simple_xpath_to_css_selector(xpath4)
    assert result4 == expected4, f"Expected '{expected4}', got '{result4}'"
def test_enhanced_css_selector_for_element():
    """
    Test the _enhanced_css_selector_for_element function to ensure that it correctly generates a CSS selector
    for a DOMElementNode. The test checks that valid class names are included, safe attributes (like 'id') are appended,
    and attributes not in the safe list are ignored.
    """
    node = DOMElementNode(
        tag_name='div',
        is_visible=True,
        parent=None,
        xpath='/html/body/div',
        attributes={
            'class': 'myClass invalid-class',
            'id': 'main',
            'non_safe': 'value',  # This attribute should be ignored because it's not in the safe list.
        },
        children=[]
    )
    result = BrowserContext._enhanced_css_selector_for_element(node, include_dynamic_attributes=True)
    expected = 'html > body > div.myClass.invalid-class[id="main"]'
    assert result == expected, f"Expected '{expected}', got '{result}'"
def test_get_initial_state():
    """
    Test that _get_initial_state of BrowserContext returns a correct initial BrowserState.
    When no page is provided, the URL should be an empty string.
    When a dummy page with a url attribute is provided, the URL should match that value.
    """
    # Define a dummy browser since BrowserContext requires one.
    class DummyBrowser:
        pass
    # Instantiate BrowserContext with a dummy browser.
    bc = BrowserContext(browser=DummyBrowser())
    # When no page is passed, expect the url value in the state to be an empty string.
    state_no_page = bc._get_initial_state()
    assert state_no_page.url == '', f"Expected empty URL when no page is provided, got: {state_no_page.url}"
    # Define a dummy page with a 'url' attribute.
    class DummyPage:
        url = "https://example.com"
    state_with_page = bc._get_initial_state(DummyPage())
    assert state_with_page.url == "https://example.com", f"Expected URL 'https://example.com', got: {state_with_page.url}"
@pytest.mark.asyncio
async def test_is_file_uploader():
    """
    Tests the is_file_uploader method to ensure it properly detects file uploader elements.
    
    Scenarios covered:
      - A DOMElementNode that is directly an <input> with type "file" should be detected as a file uploader.
      - An <input> element with type "text" should not be detected as a file uploader.
      - An element that has a child <input type="file"> should be detected as a file uploader.
      - A deeply nested uploader beyond the specified max_depth should not be detected unless the max_depth is increased.
    """
    # Create a dummy browser instance as required for BrowserContext.
    class DummyBrowser:
        pass
    # Instantiate BrowserContext with a dummy browser.
    bc = BrowserContext(browser=DummyBrowser())
    # Case 1: Direct uploader node.
    uploader_node = DOMElementNode(
        tag_name='input',
        is_visible=True,
        parent=None,
        xpath='/html/body/input',
        attributes={'type': 'file'},
        children=[]
    )
    result_direct = await bc.is_file_uploader(uploader_node)
    assert result_direct is True, "A direct file uploader input should return True."
    # Case 2: Non-uploader input node.
    non_uploader = DOMElementNode(
        tag_name='input',
        is_visible=True,
        parent=None,
        xpath='/html/body/input',
        attributes={'type': 'text'},
        children=[]
    )
    result_non = await bc.is_file_uploader(non_uploader)
    assert result_non is False, "A non-file input should return False."
    # Case 3: Uploader node as a child.
    child_uploader = DOMElementNode(
        tag_name='div',
        is_visible=True,
        parent=None,
        xpath='/html/body/div',
        attributes={},
        children=[
            DOMElementNode(
                tag_name='input',
                is_visible=True,
                parent=None,
                xpath='/html/body/div/input',
                attributes={'type': 'file'},
                children=[]
            )
        ]
    )
    result_child = await bc.is_file_uploader(child_uploader)
    assert result_child is True, "A file uploader nested within a child node should return True."
    # Case 4:
    # Test when the file uploader is nested deeper than allowed by max_depth.
    # Here, deep_child's uploader is at depth 2. We'll set max_depth=1 so the uploader is not detected.
    deep_child = DOMElementNode(
        tag_name='div',
        is_visible=True,
        parent=None,
        xpath='/html/body/div',
        attributes={},
        children=[
            DOMElementNode(
                tag_name='div',
                is_visible=True,
                parent=None,
                xpath='/html/body/div/div',
                attributes={},
                children=[
                    DOMElementNode(
                        tag_name='input',
                        is_visible=True,
                        parent=None,
                        xpath='/html/body/div/div/input',
                        attributes={'type': 'file'},
                        children=[]
                    )
                ]
            )
        ]
    )
    result_deep_limited = await bc.is_file_uploader(deep_child, max_depth=1)
    assert result_deep_limited is False, "A nested uploader beyond max_depth=1 should return False."
    # Case 5:
    # With a higher max_depth the nested uploader should be detected.
    result_deep_allowed = await bc.is_file_uploader(deep_child, max_depth=2)
    assert result_deep_allowed is True, "A nested uploader within max_depth=2 should return True."
@pytest.mark.asyncio
async def test_switch_to_tab_functionality():
    """
    Test the switch_to_tab function using a dummy session.
    This test creates a dummy BrowserContext with a dummy session that contains multiple pages.
    It verifies that switching to a tab with an allowed URL properly sets the current page,
    and that attempting to switch to a tab with a disallowed URL or an invalid index raises a BrowserError.
    """
    # Dummy classes to simulate Playwright's behavior
    class DummyPage:
        def __init__(self, url, title):
            self.url = url
            self._title = title
        async def title(self):
            return self._title
        async def wait_for_load_state(self):
            pass
        async def bring_to_front(self):
            pass
    class DummyContext:
        def __init__(self, pages):
            self.pages = pages
        async def new_page(self):
            # Returns a new dummy page with an allowed URL
            return DummyPage("http://allowed.com/new", "New Tab")
    class DummySession:
        def __init__(self, context, current_page):
            self.context = context
            self.current_page = current_page
    # Create dummy pages
    page1 = DummyPage("http://allowed.com", "Tab1")
    page2 = DummyPage("http://allowed.com/tab2", "Tab2")
    page3 = DummyPage("http://notallowed.com", "Tab3")  # This URL is not allowed
    dummy_context = DummyContext(pages=[page1, page2, page3])
    dummy_session = DummySession(context=dummy_context, current_page=page1)
    # Define a dummy browser for BrowserContext
    class DummyBrowser:
        pass
    # Instantiate BrowserContext and manually assign the dummy session
    bc = BrowserContext(browser=DummyBrowser())
    bc.session = dummy_session
    # Set configuration to allow only 'allowed.com'
    bc.config.allowed_domains = ["allowed.com"]
    # Test switching to a tab with an allowed URL (index 1)
    await bc.switch_to_tab(1)
    assert bc.session.current_page == page2, "Current tab should be switched to tab2 with allowed URL."
    # Test switching to a tab with a non-allowed URL (index 2 should raise BrowserError)
    with pytest.raises(BrowserError):
        await bc.switch_to_tab(2)
    # Test switching to an invalid negative index (should also raise BrowserError)
    with pytest.raises(BrowserError):
        await bc.switch_to_tab(-1)
@pytest.mark.asyncio
async def test_reset_context():
    """
    Test that reset_context properly resets the browser session by closing all existing pages,
    setting the cached state to an initial state, and creating a new tab.
    """
    # Create dummy classes to simulate page, context, and session behavior.
    class DummyPage:
        def __init__(self, url):
            self.url = url
            self.closed = False
        async def wait_for_load_state(self):
            pass
        async def close(self):
            self.closed = True
    class DummyContext:
        def __init__(self, pages):
            self.pages = pages
        async def new_page(self):
            # Simulate creating a new page.
            new_page = DummyPage("http://newtab.com")
            self.pages.append(new_page)
            return new_page
    class DummySession:
        def __init__(self, context, current_page):
            self.context = context
            self.current_page = current_page
            self.cached_state = None
    # Set up initial dummy pages.
    page1 = DummyPage("http://a.com")
    page2 = DummyPage("http://b.com")
    dummy_context = DummyContext(pages=[page1, page2])
    dummy_session = DummySession(context=dummy_context, current_page=page1)
    # Create a dummy browser for BrowserContext.
    class DummyBrowser:
        pass
    # Initialize BrowserContext and manually assign the dummy session.
    bc = BrowserContext(browser=DummyBrowser())
    bc.session = dummy_session
    # Call reset_context which should close existing pages and create a new tab.
    await bc.reset_context()
    # Verify that all original pages have been closed.
    for page in [page1, page2]:
        assert page.closed, "Expected original pages to be closed after reset_context call."
    # Verify that a new page has been created and set as current_page.
    assert bc.session.current_page is not None, "Expected a new current page after reset_context."
    
    # Verify that the cached state is set using _get_initial_state
    # According to _get_initial_state, if no page is provided the URL should be empty.
    state = bc.session.cached_state
    assert isinstance(state, BrowserState), "Expected cached_state to be a BrowserState instance."
    assert state.url == "", "Expected initial state's URL to be empty."
@pytest.mark.asyncio
async def test_take_screenshot_functionality():
    """
    Test that BrowserContext.take_screenshot returns a correct base64 encoded string
    from the screenshot bytes produced by a dummy page.
    """
    # Create a dummy page that returns fixed bytes when screenshot() is called.
    class DummyPage:
        async def screenshot(self, full_page: bool, animations: str):
            return b"fakeimage"
        # Dummy implementations to satisfy potential calls.
        async def wait_for_load_state(self):
            pass
    # Create a dummy session with the dummy page as current_page.
    class DummySession:
        def __init__(self, page):
            self.current_page = page
    # Create a dummy browser instance to pass to BrowserContext.
    class DummyBrowser:
        pass
    # Initialize BrowserContext with the dummy browser.
    bc = BrowserContext(browser=DummyBrowser())
    # Manually assign a dummy session containing our dummy page.
    dummy_page = DummyPage()
    dummy_session = DummySession(page=dummy_page)
    bc.session = dummy_session
    # Invoke take_screenshot and check result.
    result = await bc.take_screenshot(full_page=False)
    expected = base64.b64encode(b"fakeimage").decode("utf-8")
    assert result == expected, f"Expected base64 string '{expected}', got '{result}'"
@pytest.mark.asyncio
async def test_execute_javascript():
    """
    Test execute_javascript: verifies that the execute_javascript method returns the expected result
    from evaluating JavaScript on a dummy page.
    """
    # Create a dummy page with an evaluate method that returns a predictable result.
    class DummyPage:
        async def evaluate(self, script):
            return f"Executed: {script}"
        async def wait_for_load_state(self):
            pass
    # Create a dummy session to hold the current page.
    class DummySession:
        def __init__(self, page):
            self.current_page = page
    # Create a dummy browser instance (contents not needed for this test).
    class DummyBrowser:
        pass
    # Initialize BrowserContext with the dummy browser and assign the dummy session.
    bc = BrowserContext(browser=DummyBrowser())
    dummy_page = DummyPage()
    bc.session = DummySession(page=dummy_page)
    # Define a simple JavaScript snippet to execute.
    script = "1 + 1"
    result = await bc.execute_javascript(script)
    expected = f"Executed: {script}"
    assert result == expected, f"Expected '{expected}', got '{result}'"
@pytest.mark.asyncio
async def test_save_cookies_functionality(tmp_path):
    """
    Test that the save_cookies method properly writes preset cookies to the configured cookies_file.
    This test creates a dummy session with a dummy context that returns preset cookies,
    then invokes save_cookies and verifies the file content.
    """
    # Create a temporary file path for cookies_file
    cookies_file_path = tmp_path / "cookies.json"
    
    # Create a dummy context with a cookies() method returning preset cookies
    class DummyContext:
        def __init__(self):
            self.pages = []
        async def cookies(self):
            return [{"name": "cookie", "value": "test"}]
    
    # Create a dummy session that holds the dummy context
    class DummySession:
        def __init__(self, context):
            self.context = context
            self.current_page = None
    
    # Create a dummy browser instance (contents not needed for this test)
    class DummyBrowser:
        pass
    
    # Initialize BrowserContext with the dummy browser
    bc = BrowserContext(browser=DummyBrowser())
    # Update config to use the temporary cookies_file
    bc.config = BrowserContextConfig(cookies_file=str(cookies_file_path))
    # Manually assign a dummy session with our DummyContext
    dummy_context = DummyContext()
    bc.session = DummySession(context=dummy_context)
    
    # Invoke the save_cookies method
    await bc.save_cookies()
    
    # Verify that the cookies_file has been created and contains the expected cookies information.
    with open(str(cookies_file_path), "r") as f:
        cookies_data = json.load(f)
    
    assert cookies_data == [{"name": "cookie", "value": "test"}], f"Expected cookies data not found: {cookies_data}"
@pytest.mark.asyncio
async def test_remove_highlights_handles_exception():
    """
    Test that remove_highlights gracefully handles exceptions thrown by page.evaluate
    without propagating the error. This simulates scenarios where the page might be closed or inaccessible.
    """
    # Dummy page that simulates failure on evaluate.
    class DummyPage:
        async def evaluate(self, script):
            raise Exception("Simulated failure during evaluate")
        async def wait_for_load_state(self):
            pass
    # Dummy session holding our dummy page as current_page.
    class DummySession:
        def __init__(self, page):
            self.current_page = page
    # Dummy Browser required for BrowserContext instantiation.
    class DummyBrowser:
        pass
    # Instantiate BrowserContext using the dummy browser.
    bc = BrowserContext(browser=DummyBrowser())
    dummy_page = DummyPage()
    bc.session = DummySession(page=dummy_page)
    
    # Call remove_highlights and ensure no exception is raised.
    try:
        await bc.remove_highlights()
    except Exception as e:
        pytest.fail(f"remove_highlights() raised an exception unexpectedly: {e}")