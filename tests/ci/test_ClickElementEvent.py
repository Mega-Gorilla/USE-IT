import asyncio

import pytest
from pytest_httpserver import HTTPServer

from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.controller.service import Controller
from browser_use.controller.views import (
	ClickElementAction,
	GoToUrlAction,
)


@pytest.fixture(scope='session')
def http_server():
	"""Create and provide a test HTTP server that serves static content."""
	server = HTTPServer()
	server.start()

	# Add routes for common test pages
	server.expect_request('/').respond_with_data(
		'<html><head><title>Test Home Page</title></head><body><h1>Test Home Page</h1><p>Welcome to the test site</p></body></html>',
		content_type='text/html',
	)

	server.expect_request('/page1').respond_with_data(
		'<html><head><title>Test Page 1</title></head><body><h1>Test Page 1</h1><p>This is test page 1</p></body></html>',
		content_type='text/html',
	)

	server.expect_request('/page2').respond_with_data(
		'<html><head><title>Test Page 2</title></head><body><h1>Test Page 2</h1><p>This is test page 2</p></body></html>',
		content_type='text/html',
	)

	yield server
	server.stop()


@pytest.fixture(scope='session')
def base_url(http_server):
	"""Return the base URL for the test HTTP server."""
	return f'http://{http_server.host}:{http_server.port}'


@pytest.fixture(scope='module')
async def browser_session():
	"""Create and provide a Browser instance with security disabled."""
	browser_session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await browser_session.start()
	yield browser_session
	await browser_session.kill()


@pytest.fixture(scope='function')
def controller():
	"""Create and provide a Controller instance."""
	return Controller()


class TestClickElementEvent:
	"""Test cases for ClickElementEvent and click_element_by_index action."""

	async def test_error_handling(self, controller, browser_session):
		"""Test error handling when an action fails."""
		# Create an action with an invalid index
		invalid_action = {'click_element_by_index': ClickElementAction(index=999)}  # doesn't exist on page

		from browser_use.agent.views import ActionModel

		class ClickActionModel(ActionModel):
			click_element_by_index: ClickElementAction | None = None

		# This should fail since the element doesn't exist
		result: ActionResult = await controller.act(ClickActionModel(**invalid_action), browser_session)

		assert result.error is not None

	async def test_click_element_by_index(self, controller, browser_session, base_url, http_server):
		"""Test that click_element_by_index correctly clicks an element and handles different outcomes."""
		# Add route for clickable elements test page
		http_server.expect_request('/clickable').respond_with_data(
			"""
			<!DOCTYPE html>
			<html>
			<head>
				<title>Click Test</title>
				<style>
					.clickable {
						margin: 10px;
						padding: 10px;
						border: 1px solid #ccc;
						cursor: pointer;
					}
					#result {
						margin-top: 20px;
						padding: 10px;
						border: 1px solid #ddd;
						min-height: 20px;
					}
				</style>
			</head>
			<body>
				<h1>Click Test</h1>
				<div class="clickable" id="button1" onclick="updateResult('Button 1 clicked')">Button 1</div>
				<div class="clickable" id="button2" onclick="updateResult('Button 2 clicked')">Button 2</div>
				<a href="#" class="clickable" id="link1" onclick="updateResult('Link 1 clicked'); return false;">Link 1</a>
				<div id="result"></div>
				
				<script>
					function updateResult(text) {
						document.getElementById('result').textContent = text;
					}
				</script>
			</body>
			</html>
			""",
			content_type='text/html',
		)

		# Navigate to the clickable elements test page
		goto_action = {'go_to_url': GoToUrlAction(url=f'{base_url}/clickable', new_tab=False)}

		from browser_use.agent.views import ActionModel

		class GoToUrlActionModel(ActionModel):
			go_to_url: GoToUrlAction | None = None

		await controller.act(GoToUrlActionModel(**goto_action), browser_session)

		# Wait for the page to load
		await asyncio.sleep(0.5)  # Give page time to load

		# Initialize the DOM state to populate the selector map
		await browser_session.get_browser_state_summary(cache_clickable_elements_hashes=True)

		# Get the selector map
		selector_map = await browser_session.get_selector_map()

		# Find a clickable element in the selector map
		button_index = None
		button_text = None

		for idx, element in selector_map.items():
			# Look for the first div with class "clickable"
			if element.tag_name.lower() == 'div' and 'clickable' in str(element.attributes.get('class', '')):
				button_index = idx
				button_text = element.get_all_children_text(max_depth=2).strip()
				break

		# Verify we found a clickable element
		assert button_index is not None, (
			f'Could not find clickable element in selector map. Available elements: {[f"{idx}: {element.tag_name}" for idx, element in selector_map.items()]}'
		)

		# Define expected test data
		expected_button_text = 'Button 1'
		expected_result_text = 'Button 1 clicked'

		# Verify the button text matches what we expect
		assert button_text is not None and expected_button_text in button_text, (
			f"Expected button text '{expected_button_text}' not found in '{button_text}'"
		)

		# Create a model for the click_element_by_index action
		class ClickElementActionModel(ActionModel):
			click_element_by_index: ClickElementAction | None = None

		# Execute the action with the button index
		result = await controller.act(
			ClickElementActionModel(click_element_by_index=ClickElementAction(index=button_index)), browser_session
		)

		# Verify the result structure
		assert isinstance(result, ActionResult), 'Result should be an ActionResult instance'
		assert result.error is None, f'Expected no error but got: {result.error}'

		# Core logic validation: Verify click was successful
		assert result.extracted_content is not None
		assert f'Clicked element with index {button_index}' in result.extracted_content, (
			f'Expected click confirmation in result content, got: {result.extracted_content}'
		)
		if button_text:
			assert result.extracted_content is not None and button_text in result.extracted_content, (
				f"Button text '{button_text}' not found in result content: {result.extracted_content}"
			)

		# Verify the click actually had an effect on the page using CDP
		cdp_session = await browser_session.get_or_create_cdp_session(browser_session.agent_focus)
		result_js = await browser_session.cdp_client.send.Runtime.evaluate(
			params={'expression': "document.getElementById('result').textContent", 'returnByValue': True},
			session_id=cdp_session.session_id,
		)
		result_text = result_js.get('result', {}).get('value', '')
		assert result_text == expected_result_text, f"Expected result text '{expected_result_text}', got '{result_text}'"

	async def test_empty_css_selector_fallback(self, controller, browser_session, httpserver):
		"""Test that clicking elements with empty CSS selectors falls back to XPath."""
		# Create a test page with an element that would produce an empty CSS selector
		# This could happen with elements that have no tag name or unusual XPath structures
		httpserver.expect_request('/empty_css_test').respond_with_data(
			"""
			<html>
			<head><title>Empty CSS Selector Test</title></head>
			<body>
				<div id="container">
					<!-- Element with minimal attributes that might produce empty CSS selector -->
					<custom-element role="button" tabindex="0" style="cursor: pointer;">Click Me</custom-element>
					<div id="result">Not clicked</div>
				</div>
				<script>
					// Add click handler to the custom element
					document.querySelector('custom-element').addEventListener('click', function() {
						document.getElementById('result').textContent = 'Clicked!';
					});
				</script>
			</body>
			</html>
			""",
			content_type='text/html',
		)

		# Navigate to the test page using controller
		goto_action = {'go_to_url': GoToUrlAction(url=httpserver.url_for('/empty_css_test'), new_tab=False)}

		from browser_use.agent.views import ActionModel

		class GoToUrlActionModel(ActionModel):
			go_to_url: GoToUrlAction | None = None

		await controller.act(GoToUrlActionModel(**goto_action), browser_session)
		await asyncio.sleep(0.5)  # Give page time to load

		# Get the page state which includes clickable elements
		state = await browser_session.get_browser_state_summary(cache_clickable_elements_hashes=False)

		# Find the custom element index
		custom_element_index = None
		for index, element in state.selector_map.items():
			if element.tag_name == 'custom-element':
				custom_element_index = index
				break

		assert custom_element_index is not None, 'Could not find custom-element in selector map'

		# Mock a scenario where CSS selector generation returns empty string
		# by temporarily patching the method (we'll test the actual fallback behavior)
		original_method = browser_session._enhanced_css_selector_for_element
		empty_css_called = False

		def mock_css_selector(element, include_dynamic_attributes=True):
			nonlocal empty_css_called
			# Return empty string for our custom element to trigger fallback
			if element.tag_name == 'custom-element':
				empty_css_called = True
				return ''
			return original_method(element, include_dynamic_attributes)

		# Temporarily replace the method
		browser_session._enhanced_css_selector_for_element = mock_css_selector

		try:
			# Create click action for the custom element
			click_action = {'click_element_by_index': ClickElementAction(index=custom_element_index)}

			from browser_use.agent.views import ActionModel

			class ClickActionModel(ActionModel):
				click_element_by_index: ClickElementAction | None = None

			# Execute the click - should use XPath fallback
			result = await controller.act(ClickActionModel(**click_action), browser_session)

			# Verify the click succeeded
			assert result.error is None, f'Click failed with error: {result.error}'
			# Success field is not set for click actions, only error is set on failure
			assert empty_css_called, 'CSS selector method was not called'

			# Verify the element was actually clicked by checking the result using CDP
			cdp_session = await browser_session.get_or_create_cdp_session(browser_session.agent_focus)
			result_js = await browser_session.cdp_client.send.Runtime.evaluate(
				params={'expression': "document.getElementById('result').textContent", 'returnByValue': True},
				session_id=cdp_session.session_id,
			)
			result_text = result_js.get('result', {}).get('value', '')
			assert result_text == 'Clicked!', f'Element was not clicked, result text: {result_text}'

		finally:
			# Restore the original method
			browser_session._enhanced_css_selector_for_element = original_method

	async def test_click_element_new_tab(self, controller, browser_session, base_url, http_server):
		"""Test that click_element_by_index with new_tab=True opens links in new tabs."""
		# Add route for new tab test page
		http_server.expect_request('/newTab').respond_with_data(
			"""
			<!DOCTYPE html>
			<html>
			<head>
				<title>New Tab Test</title>
			</head>
			<body>
				<h1>New Tab Test</h1>
				<a href="/page1" id="testLink">Open Page 1</a>
			</body>
			</html>
			""",
			content_type='text/html',
		)

		# Navigate to the new tab test page
		goto_action = {'go_to_url': GoToUrlAction(url=f'{base_url}/newTab', new_tab=False)}

		from browser_use.agent.views import ActionModel

		class GoToUrlActionModel(ActionModel):
			go_to_url: GoToUrlAction | None = None

		await controller.act(GoToUrlActionModel(**goto_action), browser_session)
		await asyncio.sleep(1)  # Wait for page to load

		# Count initial tabs
		initial_tab_count = len(browser_session.tabs)

		# Get the link element (assuming it will be at index 0)
		# First get the browser state to see what elements are available
		state = await browser_session.get_browser_state_with_recovery()

		# Find the link element in the selector map
		link_index = None
		for index, element in state.selector_map.items():
			if hasattr(element, 'tag_name') and element.tag_name == 'a':
				link_index = index
				break

		assert link_index is not None, 'Could not find link element'

		# Click the link with new_tab=True
		click_action = {'click_element_by_index': ClickElementAction(index=link_index, new_tab=True)}

		class ClickActionModel(ActionModel):
			click_element_by_index: ClickElementAction | None = None

		result = await controller.act(ClickActionModel(**click_action), browser_session)
		await asyncio.sleep(1)  # Wait for new tab to open

		# Verify the result
		assert isinstance(result, ActionResult)
		assert result.extracted_content is not None

		# Verify that a new tab was opened
		final_tab_count = len(browser_session.tabs)
		assert final_tab_count == initial_tab_count + 1, f'Expected {initial_tab_count + 1} tabs, got {final_tab_count}'

		# Verify we switched to the new tab and it has the correct URL
		current_url = await browser_session.get_current_page_url()
		assert f'{base_url}/page1' in current_url

	async def test_click_element_normal_vs_new_tab(self, controller, browser_session, base_url, http_server):
		"""Test that click_element_by_index behaves differently with new_tab=False vs new_tab=True."""
		# Add route for comparison test page
		http_server.expect_request('/comparison').respond_with_data(
			"""
			<!DOCTYPE html>
			<html>
			<head>
				<title>Comparison Test</title>
			</head>
			<body>
				<h1>Comparison Test</h1>
				<a href="/page2" id="normalLink">Normal Link</a>
				<a href="/page1" id="newTabLink">New Tab Link</a>
			</body>
			</html>
			""",
			content_type='text/html',
		)

		# Navigate to the comparison test page
		goto_action = {'go_to_url': GoToUrlAction(url=f'{base_url}/comparison', new_tab=False)}

		from browser_use.agent.views import ActionModel

		class GoToUrlActionModel(ActionModel):
			go_to_url: GoToUrlAction | None = None

		await controller.act(GoToUrlActionModel(**goto_action), browser_session)
		await asyncio.sleep(1)

		initial_tab_count = len(browser_session.tabs)

		# Get browser state and find link elements
		state = await browser_session.get_browser_state_with_recovery()
		link_indices = []
		for index, element in state.selector_map.items():
			if hasattr(element, 'tag_name') and element.tag_name == 'a':
				link_indices.append(index)

		assert len(link_indices) >= 2, 'Need at least 2 links for comparison test'

		# Test normal click (new_tab=False) - should navigate in current tab
		click_action_normal = {'click_element_by_index': ClickElementAction(index=link_indices[0], new_tab=False)}

		class ClickActionModel(ActionModel):
			click_element_by_index: ClickElementAction | None = None

		result = await controller.act(ClickActionModel(**click_action_normal), browser_session)
		await asyncio.sleep(1)

		# Should still have same number of tabs
		assert len(browser_session.tabs) == initial_tab_count

		# Navigate back to comparison page for second test
		await controller.act(GoToUrlActionModel(**goto_action), browser_session)
		await asyncio.sleep(1)

		# Test new tab click (new_tab=True) - should open in new tab
		click_action_new_tab = {'click_element_by_index': ClickElementAction(index=link_indices[1], new_tab=True)}
		result = await controller.act(ClickActionModel(**click_action_new_tab), browser_session)
		await asyncio.sleep(1)

		# Should have one more tab
		assert len(browser_session.tabs) == initial_tab_count + 1
