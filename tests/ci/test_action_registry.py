"""
Comprehensive tests for the action registry system to ensure backward compatibility
and proper parameter handling for all existing patterns.

Tests cover:
1. Existing parameter patterns (individual params, pydantic models)
2. Special parameter injection (browser_session, page_extraction_llm, etc.)
3. Action-to-action calling scenarios
4. Mixed parameter patterns
5. Registry execution edge cases
"""

import asyncio
import logging

import pytest
from playwright.async_api import Page
from pydantic import Field
from pytest_httpserver import HTTPServer

from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from browser_use.controller.registry.service import Registry
from browser_use.controller.registry.views import ActionModel as BaseActionModel
from browser_use.controller.views import (
	ClickElementAction,
	InputTextAction,
	NoParamsAction,
	SearchGoogleAction,
)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class MockLLM:
	"""Mock LLM for testing"""

	async def ainvoke(self, prompt):
		class MockResponse:
			content = 'Mocked LLM response'

		return MockResponse()


class TestContext:
	"""Simple context for testing"""

	pass


# Test parameter models
class SimpleParams(BaseActionModel):
	"""Simple parameter model"""

	value: str = Field(description='Test value')


class ComplexParams(BaseActionModel):
	"""Complex parameter model with multiple fields"""

	text: str = Field(description='Text input')
	number: int = Field(description='Number input', default=42)
	optional_flag: bool = Field(description='Optional boolean', default=False)


# Test fixtures
@pytest.fixture(scope='module')
def event_loop():
	"""Create and provide an event loop for async tests."""
	loop = asyncio.get_event_loop_policy().new_event_loop()
	yield loop
	loop.close()


@pytest.fixture(scope='module')
def http_server():
	"""Create and provide a test HTTP server that serves static content."""
	server = HTTPServer()
	server.start()

	# Add a simple test page
	server.expect_request('/test').respond_with_data(
		'<html><head><title>Test Page</title></head><body><h1>Test Page</h1><p>Hello from test page</p></body></html>',
		content_type='text/html',
	)

	yield server
	server.stop()


@pytest.fixture
def base_url(http_server):
	"""Return the base URL for the test HTTP server."""
	return f'http://{http_server.host}:{http_server.port}'


@pytest.fixture(scope='module')
async def browser_session(event_loop):
	"""Create and provide a real BrowserSession instance."""
	browser_session = BrowserSession(
		headless=True,
		user_data_dir=None,
	)
	await browser_session.start()
	yield browser_session
	await browser_session.stop()


@pytest.fixture
def mock_llm():
	"""Create a mock LLM"""
	return MockLLM()


@pytest.fixture
def registry():
	"""Create a fresh registry for each test"""
	return Registry[TestContext]()


@pytest.fixture
async def test_browser(base_url):
	"""Create a real BrowserSession for testing"""
	browser_session = BrowserSession(
		headless=True,
		user_data_dir=None,
	)
	await browser_session.start()
	# Navigate to test page
	await browser_session.create_new_tab(f'{base_url}/test')
	yield browser_session
	await browser_session.stop()


class TestActionRegistryParameterPatterns:
	"""Test different parameter patterns that should all continue to work"""

	@pytest.mark.asyncio
	async def test_individual_parameters_no_browser(self, registry):
		"""Test action with individual parameters, no special injection"""

		@registry.action('Simple action with individual params')
		async def simple_action(text: str, number: int = 10):
			return ActionResult(extracted_content=f'Text: {text}, Number: {number}')

		# Test execution
		result = await registry.execute_action('simple_action', {'text': 'hello', 'number': 42})

		assert isinstance(result, ActionResult)
		assert 'Text: hello, Number: 42' in result.extracted_content

	@pytest.mark.asyncio
	async def test_individual_parameters_with_browser(self, registry, browser_session, base_url):
		"""Test action with individual parameters plus browser_session injection"""

		@registry.action('Action with individual params and browser')
		async def action_with_browser(text: str, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Text: {text}, URL: {page.url}')

		# Navigate to test page first
		await browser_session.create_new_tab(f'{base_url}/test')

		# Test execution
		result = await registry.execute_action('action_with_browser', {'text': 'hello'}, browser_session=browser_session)

		assert isinstance(result, ActionResult)
		assert 'Text: hello, URL:' in result.extracted_content
		assert base_url in result.extracted_content

	@pytest.mark.asyncio
	async def test_page_parameter_injection(self, registry, browser_session, base_url):
		"""Test action with direct Page parameter injection"""

		@registry.action('Action with page parameter')
		async def action_with_page(text: str, page: Page):
			title = await page.title()
			return ActionResult(extracted_content=f'Text: {text}, Page Title: {title}')

		# Navigate to test page first
		await browser_session.create_new_tab(f'{base_url}/test')

		# Test execution
		result = await registry.execute_action('action_with_page', {'text': 'hello'}, browser_session=browser_session)

		assert isinstance(result, ActionResult)
		assert 'Text: hello, Page Title: Test Page' in result.extracted_content

	@pytest.mark.asyncio
	async def test_pydantic_model_with_page_parameter(self, registry, browser_session, base_url):
		"""Test pydantic model action with page parameter injection"""

		@registry.action('Pydantic action with page', param_model=ComplexParams)
		async def pydantic_action_with_page(params: ComplexParams, page: Page):
			title = await page.title()
			return ActionResult(extracted_content=f'Text: {params.text}, Number: {params.number}, Page Title: {title}')

		# Navigate to test page first
		await browser_session.create_new_tab(f'{base_url}/test')

		# Test execution
		result = await registry.execute_action(
			'pydantic_action_with_page', {'text': 'test', 'number': 100}, browser_session=browser_session
		)

		assert isinstance(result, ActionResult)
		assert 'Text: test, Number: 100, Page Title: Test Page' in result.extracted_content

	@pytest.mark.asyncio
	async def test_pydantic_model_parameters(self, registry, browser_session, base_url):
		"""Test action that takes a pydantic model as first parameter"""

		@registry.action('Action with pydantic model', param_model=ComplexParams)
		async def pydantic_action(params: ComplexParams, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(
				extracted_content=f'Text: {params.text}, Number: {params.number}, Flag: {params.optional_flag}, URL: {page.url}'
			)

		# Navigate to test page first
		await browser_session.create_new_tab(f'{base_url}/test')

		# Test execution
		result = await registry.execute_action(
			'pydantic_action', {'text': 'test', 'number': 100, 'optional_flag': True}, browser_session=browser_session
		)

		assert isinstance(result, ActionResult)
		assert 'Text: test, Number: 100, Flag: True' in result.extracted_content
		assert base_url in result.extracted_content

	@pytest.mark.asyncio
	async def test_mixed_special_parameters(self, registry, browser_session, base_url, mock_llm):
		"""Test action with multiple special injected parameters"""

		from langchain_core.language_models.chat_models import BaseChatModel

		@registry.action('Action with multiple special params')
		async def multi_special_action(
			text: str,
			browser_session: BrowserSession,
			page_extraction_llm: BaseChatModel,
			available_file_paths: list,
		):
			page = await browser_session.get_current_page()
			llm_response = await page_extraction_llm.ainvoke('test')
			files = available_file_paths or []

			return ActionResult(
				extracted_content=f'Text: {text}, URL: {page.url}, LLM: {llm_response.content}, Files: {len(files)}'
			)

		# Navigate to test page first
		await browser_session.create_new_tab(f'{base_url}/test')

		# Test execution
		result = await registry.execute_action(
			'multi_special_action',
			{'text': 'hello'},
			browser_session=browser_session,
			page_extraction_llm=mock_llm,
			available_file_paths=['file1.txt', 'file2.txt'],
		)

		assert isinstance(result, ActionResult)
		assert 'Text: hello' in result.extracted_content
		assert base_url in result.extracted_content
		assert 'LLM: Mocked LLM response' in result.extracted_content
		assert 'Files: 2' in result.extracted_content

	@pytest.mark.asyncio
	async def test_no_params_action(self, registry, test_browser):
		"""Test action with NoParamsAction model"""

		@registry.action('No params action', param_model=NoParamsAction)
		async def no_params_action(params: NoParamsAction, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'No params action executed on {page.url}')

		# Test execution with any parameters (should be ignored)
		result = await registry.execute_action(
			'no_params_action', {'random': 'data', 'should': 'be', 'ignored': True}, browser_session=test_browser
		)

		assert isinstance(result, ActionResult)
		assert 'No params action executed on' in result.extracted_content
		assert '/test' in result.extracted_content

	@pytest.mark.asyncio
	async def test_legacy_browser_parameter_names(self, registry, test_browser):
		"""Test that legacy browser parameter names still work"""

		@registry.action('Action with legacy browser param')
		async def legacy_browser_action(text: str, browser: BrowserSession):
			page = await browser.get_current_page()
			return ActionResult(extracted_content=f'Legacy browser: {text}, URL: {page.url}')

		@registry.action('Action with legacy browser_context param')
		async def legacy_context_action(text: str, browser_context: BrowserSession):
			page = await browser_context.get_current_page()
			return ActionResult(extracted_content=f'Legacy context: {text}, URL: {page.url}')

		# Test legacy browser parameter
		result1 = await registry.execute_action('legacy_browser_action', {'text': 'test1'}, browser_session=test_browser)
		assert 'Legacy browser: test1, URL:' in result1.extracted_content
		assert '/test' in result1.extracted_content

		# Test legacy browser_context parameter
		result2 = await registry.execute_action('legacy_context_action', {'text': 'test2'}, browser_session=test_browser)
		assert 'Legacy context: test2, URL:' in result2.extracted_content
		assert '/test' in result2.extracted_content

	@pytest.mark.asyncio
	async def test_page_parameter_optimization(self, test_browser: BrowserSession, httpserver: HTTPServer):
		"""Test that actions can use page: Page parameter directly instead of browser_session"""
		registry = Registry()

		httpserver.expect_request('/test').respond_with_data('<html><body>Test Page</body></html>')
		page = await test_browser.get_current_page()
		await page.goto(httpserver.url_for('/test'))

		# Action that takes page directly (optimized pattern)
		@registry.action('Action with direct page parameter')
		async def direct_page_action(text: str, page: Page):
			# This is the optimized pattern - no need to call get_current_page()
			return ActionResult(extracted_content=f'Direct page: {text}, URL: {page.url}')

		# Action that takes browser_session and calls get_current_page (old pattern)
		@registry.action('Action with browser_session parameter')
		async def browser_session_action(text: str, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Browser session: {text}, URL: {page.url}')

		# Test direct page parameter
		result1 = await registry.execute_action('direct_page_action', {'text': 'optimized'}, browser_session=test_browser)
		assert 'Direct page: optimized, URL:' in result1.extracted_content
		assert '/test' in result1.extracted_content

		# Test browser_session parameter (should still work)
		result2 = await registry.execute_action('browser_session_action', {'text': 'legacy'}, browser_session=test_browser)
		assert 'Browser session: legacy, URL:' in result2.extracted_content
		assert '/test' in result2.extracted_content

		# Verify both patterns work with pydantic models too
		class PageActionParams(BaseActionModel):
			message: str = Field(..., description='Test message')

		@registry.action('Pydantic action with page', param_model=PageActionParams)
		async def pydantic_page_action(params: PageActionParams, page: Page):
			return ActionResult(extracted_content=f'Pydantic page: {params.message}, URL: {page.url}')

		result3 = await registry.execute_action('pydantic_page_action', {'message': 'pydantic'}, browser_session=test_browser)
		assert 'Pydantic page: pydantic, URL:' in result3.extracted_content
		assert '/test' in result3.extracted_content


class TestActionToActionCalling:
	"""Test scenarios where actions call other actions"""

	@pytest.mark.asyncio
	async def test_action_calling_action_with_kwargs(self, registry, test_browser):
		"""Test action calling another action using kwargs (current problematic pattern)"""

		# Helper function that actions can call
		async def helper_function(browser_session: BrowserSession, data: str):
			page = await browser_session.get_current_page()
			return f'Helper processed: {data} on {page.url}'

		@registry.action('First action')
		async def first_action(text: str, browser_session: BrowserSession):
			# This should work without parameter conflicts
			result = await helper_function(browser_session=browser_session, data=text)
			return ActionResult(extracted_content=f'First: {result}')

		@registry.action('Calling action')
		async def calling_action(message: str, browser_session: BrowserSession):
			# Call the first action through the registry (simulates action-to-action calling)
			intermediate_result = await registry.execute_action(
				'first_action', {'text': message}, browser_session=browser_session
			)
			return ActionResult(extracted_content=f'Called result: {intermediate_result.extracted_content}')

		# Test the calling chain
		result = await registry.execute_action('calling_action', {'message': 'test'}, browser_session=test_browser)

		assert isinstance(result, ActionResult)
		assert 'Called result: First: Helper processed: test on' in result.extracted_content
		assert '/test' in result.extracted_content

	@pytest.mark.asyncio
	async def test_google_sheets_style_calling_pattern(self, registry, test_browser):
		"""Test the specific pattern from Google Sheets actions that causes the error"""

		# Simulate the _select_cell_or_range helper function
		async def _select_cell_or_range(browser_session: BrowserSession, cell_or_range: str):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Selected cell {cell_or_range} on {page.url}')

		@registry.action('Select cell or range')
		async def select_cell_or_range(cell_or_range: str, browser_session: BrowserSession):
			# This pattern now works with kwargs
			return await _select_cell_or_range(browser_session=browser_session, cell_or_range=cell_or_range)

		@registry.action('Select cell or range (fixed)')
		async def select_cell_or_range_fixed(cell_or_range: str, browser_session: BrowserSession):
			# This pattern also works
			return await _select_cell_or_range(browser_session, cell_or_range)

		@registry.action('Update range contents')
		async def update_range_contents(range_name: str, new_contents: str, browser_session: BrowserSession):
			# This action calls select_cell_or_range, simulating the real Google Sheets pattern
			# Get the action's param model to call it properly
			action = registry.registry.actions['select_cell_or_range_fixed']
			params = action.param_model(cell_or_range=range_name)
			await select_cell_or_range_fixed(params=params, browser_session=browser_session)
			return ActionResult(extracted_content=f'Updated range {range_name} with {new_contents}')

		# Test the fixed version (should work)
		result_fixed = await registry.execute_action(
			'select_cell_or_range_fixed', {'cell_or_range': 'A1:F100'}, browser_session=test_browser
		)
		assert 'Selected cell A1:F100 on' in result_fixed.extracted_content
		assert '/test' in result_fixed.extracted_content

		# Test the chained calling pattern
		result_chain = await registry.execute_action(
			'update_range_contents', {'range_name': 'B2:D4', 'new_contents': 'test data'}, browser_session=test_browser
		)
		assert 'Updated range B2:D4 with test data' in result_chain.extracted_content

		# Test the problematic version (should work with enhanced registry)
		result_problematic = await registry.execute_action(
			'select_cell_or_range', {'cell_or_range': 'A1:F100'}, browser_session=test_browser
		)
		# With the enhanced registry, this should succeed
		assert 'Selected cell A1:F100 on' in result_problematic.extracted_content
		assert '/test' in result_problematic.extracted_content

	@pytest.mark.asyncio
	async def test_complex_action_chain(self, registry, test_browser):
		"""Test a complex chain of actions calling other actions"""

		@registry.action('Base action')
		async def base_action(value: str, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Base: {value} on {page.url}')

		@registry.action('Middle action')
		async def middle_action(input_val: str, browser_session: BrowserSession):
			# Call base action
			base_result = await registry.execute_action(
				'base_action', {'value': f'processed-{input_val}'}, browser_session=browser_session
			)
			return ActionResult(extracted_content=f'Middle: {base_result.extracted_content}')

		@registry.action('Top action')
		async def top_action(original: str, browser_session: BrowserSession):
			# Call middle action
			middle_result = await registry.execute_action(
				'middle_action', {'input_val': f'enhanced-{original}'}, browser_session=browser_session
			)
			return ActionResult(extracted_content=f'Top: {middle_result.extracted_content}')

		# Test the full chain
		result = await registry.execute_action('top_action', {'original': 'test'}, browser_session=test_browser)

		assert isinstance(result, ActionResult)
		assert 'Top: Middle: Base: processed-enhanced-test on' in result.extracted_content
		assert '/test' in result.extracted_content


class TestRegistryEdgeCases:
	"""Test edge cases and error conditions"""

	@pytest.mark.asyncio
	async def test_decorated_action_rejects_positional_args(self, registry, test_browser):
		"""Test that decorated actions reject positional arguments"""

		@registry.action('Action that should reject positional args')
		async def test_action(cell_or_range: str, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Selected cell {cell_or_range} on {page.url}')

		# Test that calling with positional arguments raises TypeError
		with pytest.raises(
			TypeError, match='test_action\\(\\) does not accept positional arguments, only keyword arguments are allowed'
		):
			await test_action(test_browser, 'A1:B2')

		# Test that calling with keyword arguments works
		result = await test_action(browser_session=test_browser, cell_or_range='A1:B2')
		assert isinstance(result, ActionResult)
		assert 'Selected cell A1:B2 on' in result.extracted_content

	@pytest.mark.asyncio
	async def test_missing_required_browser_session(self, registry):
		"""Test that actions requiring browser_session fail appropriately when not provided"""

		@registry.action('Requires browser')
		async def requires_browser(text: str, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Text: {text}, URL: {page.url}')

		# Should raise RuntimeError when browser_session is required but not provided
		with pytest.raises(RuntimeError, match='requires browser_session but none provided'):
			await registry.execute_action(
				'requires_browser',
				{'text': 'test'},
				# No browser_session provided
			)

	@pytest.mark.asyncio
	async def test_missing_required_llm(self, registry, test_browser):
		"""Test that actions requiring page_extraction_llm fail appropriately when not provided"""

		from langchain_core.language_models.chat_models import BaseChatModel

		@registry.action('Requires LLM')
		async def requires_llm(text: str, browser_session: BrowserSession, page_extraction_llm: BaseChatModel):
			page = await browser_session.get_current_page()
			llm_response = await page_extraction_llm.ainvoke('test')
			return ActionResult(extracted_content=f'Text: {text}, LLM: {llm_response.content}')

		# Should raise RuntimeError when page_extraction_llm is required but not provided
		with pytest.raises(RuntimeError, match='requires page_extraction_llm but none provided'):
			await registry.execute_action(
				'requires_llm',
				{'text': 'test'},
				browser_session=test_browser,
				# No page_extraction_llm provided
			)

	@pytest.mark.asyncio
	async def test_invalid_parameters(self, registry, test_browser):
		"""Test handling of invalid parameters"""

		@registry.action('Typed action')
		async def typed_action(number: int, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Number: {number}')

		# Should raise RuntimeError when parameter validation fails
		with pytest.raises(RuntimeError, match='Invalid parameters'):
			await registry.execute_action(
				'typed_action',
				{'number': 'not a number'},  # Invalid type
				browser_session=test_browser,
			)

	@pytest.mark.asyncio
	async def test_nonexistent_action(self, registry, test_browser):
		"""Test calling a non-existent action"""

		with pytest.raises(ValueError, match='Action nonexistent_action not found'):
			await registry.execute_action('nonexistent_action', {'param': 'value'}, browser_session=test_browser)

	@pytest.mark.asyncio
	async def test_sync_action_wrapper(self, registry, test_browser):
		"""Test that sync functions are properly wrapped to be async"""

		@registry.action('Sync action')
		def sync_action(text: str, browser_session: BrowserSession):
			# This is a sync function that should be wrapped
			return ActionResult(extracted_content=f'Sync: {text}')

		# Should work even though the original function is sync
		result = await registry.execute_action('sync_action', {'text': 'test'}, browser_session=test_browser)

		assert isinstance(result, ActionResult)
		assert 'Sync: test' in result.extracted_content

	@pytest.mark.asyncio
	async def test_excluded_actions(self, test_browser):
		"""Test that excluded actions are not registered"""

		registry_with_exclusions = Registry[TestContext](exclude_actions=['excluded_action'])

		@registry_with_exclusions.action('Excluded action')
		async def excluded_action(text: str):
			return ActionResult(extracted_content=f'Should not execute: {text}')

		@registry_with_exclusions.action('Included action')
		async def included_action(text: str):
			return ActionResult(extracted_content=f'Should execute: {text}')

		# Excluded action should not be in registry
		assert 'excluded_action' not in registry_with_exclusions.registry.actions
		assert 'included_action' in registry_with_exclusions.registry.actions

		# Should raise error when trying to execute excluded action
		with pytest.raises(ValueError, match='Action excluded_action not found'):
			await registry_with_exclusions.execute_action('excluded_action', {'text': 'test'})

		# Included action should work
		result = await registry_with_exclusions.execute_action('included_action', {'text': 'test'})
		assert 'Should execute: test' in result.extracted_content


class TestExistingControllerActions:
	"""Test that existing controller actions continue to work"""

	@pytest.mark.asyncio
	async def test_existing_action_models(self, registry, test_browser):
		"""Test that existing action parameter models work correctly"""

		@registry.action('Test search', param_model=SearchGoogleAction)
		async def test_search(params: SearchGoogleAction, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Searched for: {params.query}')

		@registry.action('Test click', param_model=ClickElementAction)
		async def test_click(params: ClickElementAction, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Clicked element: {params.index}')

		@registry.action('Test input', param_model=InputTextAction)
		async def test_input(params: InputTextAction, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Input text: {params.text} at index: {params.index}')

		# Test SearchGoogleAction
		result1 = await registry.execute_action('test_search', {'query': 'python testing'}, browser_session=test_browser)
		assert 'Searched for: python testing' in result1.extracted_content

		# Test ClickElementAction
		result2 = await registry.execute_action('test_click', {'index': 42}, browser_session=test_browser)
		assert 'Clicked element: 42' in result2.extracted_content

		# Test InputTextAction
		result3 = await registry.execute_action('test_input', {'index': 5, 'text': 'test input'}, browser_session=test_browser)
		assert 'Input text: test input at index: 5' in result3.extracted_content

	@pytest.mark.asyncio
	async def test_pydantic_vs_individual_params_consistency(self, registry, test_browser):
		"""Test that pydantic and individual parameter patterns produce consistent results"""

		# Action using individual parameters
		@registry.action('Individual params')
		async def individual_params_action(text: str, number: int, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Individual: {text}-{number}')

		# Action using pydantic model
		class TestParams(BaseActionModel):
			text: str
			number: int

		@registry.action('Pydantic params', param_model=TestParams)
		async def pydantic_params_action(params: TestParams, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Pydantic: {params.text}-{params.number}')

		# Both should produce similar results
		test_data = {'text': 'hello', 'number': 42}

		result1 = await registry.execute_action('individual_params_action', test_data, browser_session=test_browser)

		result2 = await registry.execute_action('pydantic_params_action', test_data, browser_session=test_browser)

		# Both should extract the same content (just different prefixes)
		assert 'hello-42' in result1.extracted_content
		assert 'hello-42' in result2.extracted_content
		assert 'Individual:' in result1.extracted_content
		assert 'Pydantic:' in result2.extracted_content


# Test runner for manual execution
if __name__ == '__main__':
	# Run a simple test manually
	import asyncio

	async def manual_test():
		"""Manual test runner for debugging"""
		print('Running manual test...')

		registry = Registry[TestContext]()
		browser_session = BrowserSession(headless=True)
		await browser_session.start()
		await browser_session.create_new_tab('https://example.com')

		@registry.action('Manual test action')
		async def manual_action(text: str, browser_session: BrowserSession):
			page = await browser_session.get_current_page()
			return ActionResult(extracted_content=f'Manual: {text} on {page.url}')

		result = await registry.execute_action('manual_action', {'text': 'test'}, browser_session=browser_session)

		print(f'Result: {result.extracted_content}')
		await browser_session.stop()
		print('Manual test passed!')

	if __name__ == '__main__':
		asyncio.run(manual_test())
