"""
Tests for action registration normalization and validation.
Ensures both Type 1 and Type 2 patterns work, and new validation rules are enforced.
"""

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from playwright.async_api import Page
from pydantic import BaseModel

from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from browser_use.controller.registry.service import Registry


class MockBrowserSession(BrowserSession):
	"""Mock BrowserSession for testing"""

	def __init__(self):
		# Don't call super().__init__() to avoid real browser
		pass


class MockPage:
	"""Mock Page for testing"""

	pass


class MockLLM(BaseChatModel):
	"""Mock LLM for testing"""

	def _generate(self, *args, **kwargs):
		pass

	async def _agenerate(self, *args, **kwargs):
		pass


# Test Type 1 Pattern (Pydantic model first)
class TestType1Pattern:
	def test_type1_with_param_model(self):
		"""Type 1: action(params: Model, special_args...) should work"""
		registry = Registry()

		class ClickAction(BaseModel):
			index: int
			delay: float = 0.0

		@registry.action('Click element', param_model=ClickAction)
		async def click_element(params: ClickAction, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Clicked {params.index}')

		# Verify registration
		assert 'click_element' in registry.registry.actions
		action = registry.registry.actions['click_element']
		assert action.param_model == ClickAction

		# Verify decorated function signature (should be kwargs-only)
		import inspect

		sig = inspect.signature(click_element)
		params = list(sig.parameters.values())

		# Should have no positional-only or positional-or-keyword params
		for param in params:
			assert param.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD)

	def test_type1_with_multiple_special_params(self):
		"""Type 1 with multiple special params should work"""
		registry = Registry()

		class ExtractAction(BaseModel):
			goal: str
			include_links: bool = False

		@registry.action('Extract content', param_model=ExtractAction)
		async def extract_content(
			params: ExtractAction, browser_session: BrowserSession, page: Page, page_extraction_llm: BaseChatModel
		):
			return ActionResult(extracted_content=params.goal)

		assert 'extract_content' in registry.registry.actions


# Test Type 2 Pattern (loose parameters)
class TestType2Pattern:
	def test_type2_simple_action(self):
		"""Type 2: action(arg1, arg2, special_args...) should work"""
		registry = Registry()

		@registry.action('Fill field')
		async def fill_field(index: int, text: str, page: Page):
			return ActionResult(extracted_content=f'Filled {index} with {text}')

		# Verify registration
		assert 'fill_field' in registry.registry.actions
		action = registry.registry.actions['fill_field']

		# Should auto-generate param model
		assert action.param_model is not None
		assert hasattr(action.param_model, 'index')
		assert hasattr(action.param_model, 'text')

	def test_type2_with_defaults(self):
		"""Type 2 with default values should preserve defaults"""
		registry = Registry()

		@registry.action('Scroll page')
		async def scroll_page(direction: str = 'down', amount: int = 100, browser_session: BrowserSession = None):
			return ActionResult(extracted_content=f'Scrolled {direction} by {amount}')

		action = registry.registry.actions['scroll_page']
		# Check that defaults are preserved in generated model
		schema = action.param_model.model_json_schema()
		assert schema['properties']['direction']['default'] == 'down'
		assert schema['properties']['amount']['default'] == 100

	def test_type2_no_action_params(self):
		"""Type 2 with only special params should work"""
		registry = Registry()

		@registry.action('Save PDF')
		async def save_pdf(browser_session: BrowserSession, page: Page):
			return ActionResult(extracted_content='Saved PDF')

		action = registry.registry.actions['save_pdf']
		# Should have empty or minimal param model
		fields = action.param_model.model_fields
		assert len(fields) == 0 or all(f in ['title'] for f in fields)


# Test validation rules
class TestValidationRules:
	def test_error_on_kwargs_in_original_function(self):
		"""Should error if original function has kwargs"""
		registry = Registry()

		with pytest.raises(ValueError, match='kwargs.*not allowed'):

			@registry.action('Bad action')
			async def bad_action(index: int, page: Page, **kwargs):
				pass

	def test_error_on_special_param_name_with_wrong_type(self):
		"""Should error if special param name used with wrong type"""
		registry = Registry()

		# Using 'page' with str type should error
		with pytest.raises(ValueError, match='conflicts with special argument.*page: Page'):

			@registry.action('Navigate')
			async def navigate_to_page(page: str, browser_session: BrowserSession):
				pass

		# Using 'browser_session' with wrong type should error
		with pytest.raises(ValueError, match='conflicts with special argument.*browser_session: BrowserSession'):

			@registry.action('Bad session')
			async def bad_session(browser_session: str):
				pass

	def test_special_params_must_match_type(self):
		"""Special params with correct types should work"""
		registry = Registry()

		@registry.action('Good action')
		async def good_action(
			index: int,
			page: Page,  # Correct type
			browser_session: BrowserSession,  # Correct type
		):
			return ActionResult()

		assert 'good_action' in registry.registry.actions


# Test decorated function behavior
class TestDecoratedFunctionBehavior:
	def test_decorated_function_only_accepts_kwargs(self):
		"""Decorated functions should only accept kwargs, no positional args"""
		registry = Registry()

		@registry.action('Click')
		async def click(index: int, browser_session: BrowserSession):
			return ActionResult()

		# Should raise error when called with positional args
		with pytest.raises(TypeError, match='positional arguments'):
			import asyncio

			asyncio.run(click(5, MockBrowserSession()))

	def test_decorated_function_accepts_params_model(self):
		"""Decorated function should accept params as model"""
		registry = Registry()

		@registry.action('Input text')
		async def input_text(index: int, text: str, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'{index}:{text}')

		# Get the generated param model class
		action = registry.registry.actions['input_text']
		ParamsModel = action.param_model

		# Should work with params model
		import asyncio

		result = asyncio.run(input_text(params=ParamsModel(index=5, text='hello'), browser_session=MockBrowserSession()))
		assert result.extracted_content == '5:hello'

	def test_decorated_function_ignores_extra_kwargs(self):
		"""Decorated function should ignore extra kwargs for easy unpacking"""
		registry = Registry()

		@registry.action('Simple action')
		async def simple_action(value: int, page: Page):
			return ActionResult(extracted_content=str(value))

		# Should work even with extra kwargs
		special_context = {
			'page': MockPage(),
			'browser_session': MockBrowserSession(),
			'page_extraction_llm': MockLLM(),
			'context': {'extra': 'data'},
			'unknown_param': 'ignored',
		}

		action = registry.registry.actions['simple_action']
		ParamsModel = action.param_model

		import asyncio

		result = asyncio.run(simple_action(params=ParamsModel(value=42), **special_context))
		assert result.extracted_content == '42'


# Test params model generation
class TestParamsModelGeneration:
	def test_generates_model_from_non_special_args(self):
		"""Should generate param model from non-special positional args"""
		registry = Registry()

		@registry.action('Complex action')
		async def complex_action(
			query: str, max_results: int, include_images: bool = True, page: Page = None, browser_session: BrowserSession = None
		):
			return ActionResult()

		action = registry.registry.actions['complex_action']
		model_fields = action.param_model.model_fields

		# Should include only non-special params
		assert 'query' in model_fields
		assert 'max_results' in model_fields
		assert 'include_images' in model_fields

		# Should NOT include special params
		assert 'page' not in model_fields
		assert 'browser_session' not in model_fields

	def test_preserves_type_annotations(self):
		"""Generated model should preserve type annotations"""
		registry = Registry()

		@registry.action('Typed action')
		async def typed_action(
			count: int, rate: float, enabled: bool, name: str | None = None, browser_session: BrowserSession = None
		):
			return ActionResult()

		action = registry.registry.actions['typed_action']
		schema = action.param_model.model_json_schema()

		# Check types are preserved
		assert schema['properties']['count']['type'] == 'integer'
		assert schema['properties']['rate']['type'] == 'number'
		assert schema['properties']['enabled']['type'] == 'boolean'
		# Optional should allow null
		assert 'null' in schema['properties']['name']['anyOf'][1]['type']


# Test error messages
class TestErrorMessages:
	def test_clear_error_for_kwargs(self):
		"""Error message for kwargs should be clear"""
		registry = Registry()

		try:

			@registry.action('Bad')
			async def bad(x: int, **kwargs):
				pass

			pytest.fail('Should have raised ValueError')
		except ValueError as e:
			assert 'kwargs' in str(e).lower()
			assert 'not allowed' in str(e).lower()
			assert 'bad' in str(e).lower()  # Should mention function name

	def test_clear_error_for_param_conflicts(self):
		"""Error message for param conflicts should be helpful"""
		registry = Registry()

		try:

			@registry.action('Bad')
			async def bad(page: str):
				pass

			pytest.fail('Should have raised ValueError')
		except ValueError as e:
			error_msg = str(e)
			assert 'page: str' in error_msg
			assert 'conflicts' in error_msg
			assert 'page: Page' in error_msg  # Show expected type
			assert 'bad' in error_msg.lower()  # Show function name


# Integration test with execute_action
class TestExecuteAction:
	@pytest.mark.asyncio
	async def test_execute_with_special_params(self):
		"""execute_action should work with new normalized functions"""
		registry = Registry()

		@registry.action('Test action')
		async def test_action(value: str, multiplier: int = 2, browser_session: BrowserSession = None):
			return ActionResult(extracted_content=f'{value * multiplier}')

		# Execute the action
		result = await registry.execute_action(
			action_name='test_action', params={'value': 'hi', 'multiplier': 3}, browser_session=MockBrowserSession()
		)

		assert result.extracted_content == 'hihihi'

	@pytest.mark.asyncio
	async def test_execute_without_params(self):
		"""execute_action should work for actions with no params"""
		registry = Registry()

		@registry.action('No params')
		async def no_params(browser_session: BrowserSession):
			return ActionResult(extracted_content='done')

		result = await registry.execute_action(action_name='no_params', params={}, browser_session=MockBrowserSession())

		assert result.extracted_content == 'done'


# Test calling decorated actions from within other actions
class TestInterActionCalls:
	@pytest.mark.asyncio
	async def test_action_calling_another_action(self):
		"""Actions should be able to call other decorated actions using kwargs"""
		registry = Registry()

		@registry.action('Helper action')
		async def helper_action(value: int, browser_session: BrowserSession):
			return ActionResult(extracted_content=f'Helper: {value}')

		@registry.action('Main action')
		async def main_action(multiplier: int, browser_session: BrowserSession):
			# Get the param model for helper action
			helper_model = registry.registry.actions['helper_action'].param_model

			# Call helper action with kwargs
			result = await helper_action(params=helper_model(value=10 * multiplier), browser_session=browser_session)
			return ActionResult(extracted_content=f'Main -> {result.extracted_content}')

		# Execute main action
		result = await registry.execute_action(
			action_name='main_action', params={'multiplier': 3}, browser_session=MockBrowserSession()
		)

		assert result.extracted_content == 'Main -> Helper: 30'

	@pytest.mark.asyncio
	async def test_action_unpacking_special_context(self):
		"""Actions should be able to unpack all special context when calling others"""
		registry = Registry()

		@registry.action('Inner action')
		async def inner_action(text: str, page: Page, browser_session: BrowserSession, page_extraction_llm: BaseChatModel):
			return ActionResult(extracted_content=f'Inner: {text}, has page: {page is not None}')

		@registry.action('Outer action')
		async def outer_action(browser_session: BrowserSession, page: Page):
			# Create special context dict
			special_context = {
				'browser_session': browser_session,
				'page': page,
				'page_extraction_llm': MockLLM(),
				'context': None,  # Extra param that will be ignored
				'has_sensitive_data': False,  # Also ignored
			}

			inner_model = registry.registry.actions['inner_action'].param_model

			# Should be able to unpack entire context
			result = await inner_action(params=inner_model(text='Hello'), **special_context)
			return result

		result = await registry.execute_action(
			action_name='outer_action', params={}, browser_session=MockBrowserSession(), page=MockPage()
		)

		assert 'Inner: Hello, has page: True' in result.extracted_content


# Test mixed ordering of parameters
class TestParameterOrdering:
	def test_mixed_param_ordering(self):
		"""Should handle any ordering of action params and special params"""
		registry = Registry()

		# Special params mixed throughout
		@registry.action('Mixed params')
		async def mixed_action(
			first: str,
			browser_session: BrowserSession,
			second: int,
			page: Page,
			third: bool = True,
			page_extraction_llm: BaseChatModel = None,
		):
			return ActionResult()

		action = registry.registry.actions['mixed_action']
		model_fields = action.param_model.model_fields

		# Only action params in model
		assert set(model_fields.keys()) == {'first', 'second', 'third'}
		assert model_fields['third'].default is True

	def test_all_params_at_end(self):
		"""Should work with all action params at the end"""
		registry = Registry()

		@registry.action('Params at end')
		async def params_at_end(browser_session: BrowserSession, page: Page, query: str, limit: int = 10):
			return ActionResult()

		action = registry.registry.actions['params_at_end']
		assert set(action.param_model.model_fields.keys()) == {'query', 'limit'}


if __name__ == '__main__':
	pytest.main([__file__, '-v'])
