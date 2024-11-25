import logging

from main_content_extractor import MainContentExtractor

from browser_use.agent.views import ActionModel, ActionResult
from browser_use.browser.service import Browser
from browser_use.controller.registry.service import Registry
from browser_use.controller.views import (
	ClickElementAction,
	DoneAction,
	ExtractPageContentAction,
	GoToUrlAction,
	InputTextAction,
	OpenTabAction,
	ScrollAction,
	SearchGoogleAction,
	SwitchTabAction,
)
from browser_use.utils import time_execution_sync

logger = logging.getLogger(__name__)


class Controller:
	def __init__(
		self, headless: bool = False, keep_open: bool = False, cookies_path: str | None = None
	):
		self.browser = Browser(headless=headless, keep_open=keep_open, cookies_path=cookies_path)
		self.registry = Registry()
		self._register_default_actions()

	def _register_default_actions(self):
		"""Register all default browser actions"""

		# Basic Navigation Actions
		@self.registry.action(
			'Search Google', param_model=SearchGoogleAction, requires_browser=True
		)
		async def search_google(params: SearchGoogleAction, browser: Browser):
			page = await browser.get_current_page()
			await page.goto(f'https://www.google.com/search?q={params.query}')
			await browser.wait_for_page_load()

		@self.registry.action('Navigate to URL', param_model=GoToUrlAction, requires_browser=True)
		async def go_to_url(params: GoToUrlAction, browser: Browser):
			page = await browser.get_current_page()
			await page.goto(params.url)
			await browser.wait_for_page_load()

		@self.registry.action('Go back', requires_browser=True)
		async def go_back(browser: Browser):
			page = await browser.get_current_page()
			await page.go_back()
			await browser.wait_for_page_load()

		# Element Interaction Actions
		@self.registry.action(
			'Click element', param_model=ClickElementAction, requires_browser=True
		)
		async def click_element(params: ClickElementAction, browser: Browser):
			session = await browser.get_session()
			state = session.cached_state

			if params.index not in state.selector_map:
				print(state.selector_map)
				raise Exception(
					f'Element with index {params.index} does not exist - retry or use alternative actions'
				)

			xpath = state.selector_map[params.index]
			initial_pages = len(session.context.pages)

			msg = None

			for _ in range(params.num_clicks):
				try:
					await browser._click_element_by_xpath(xpath)
					msg = f'🖱️  Clicked element {params.index}: {xpath}'
					if params.num_clicks > 1:
						msg += f' ({_ + 1}/{params.num_clicks} clicks)'
				except Exception as e:
					logger.warning(f'Element no longer available after {_ + 1} clicks: {str(e)}')
					break

			if len(session.context.pages) > initial_pages:
				await browser.switch_to_tab(-1)

			return ActionResult(extracted_content=f'{msg}')

		@self.registry.action('Input text', param_model=InputTextAction, requires_browser=True)
		async def input_text(params: InputTextAction, browser: Browser):
			session = await browser.get_session()
			state = session.cached_state

			if params.index not in state.selector_map:
				raise Exception(
					f'Element index {params.index} does not exist - retry or use alternative actions'
				)

			xpath = state.selector_map[params.index]
			await browser._input_text_by_xpath(xpath, params.text)
			msg = f'⌨️  Input "{params.text}" into {params.index}: {xpath}'
			return ActionResult(extracted_content=msg)

		# Tab Management Actions
		@self.registry.action('Switch tab', param_model=SwitchTabAction, requires_browser=True)
		async def switch_tab(params: SwitchTabAction, browser: Browser):
			await browser.switch_to_tab(params.page_id)
			# Wait for tab to be ready
			await browser.wait_for_page_load()

		@self.registry.action('Open new tab', param_model=OpenTabAction, requires_browser=True)
		async def open_tab(params: OpenTabAction, browser: Browser):
			await browser.create_new_tab(params.url)

		# Content Actions
		@self.registry.action(
			'Extract page content to get the text or markdown ',
			param_model=ExtractPageContentAction,
			requires_browser=True,
		)
		async def extract_content(params: ExtractPageContentAction, browser: Browser):
			page = await browser.get_current_page()

			content = MainContentExtractor.extract(  # type: ignore
				html=await page.content(),
				output_format=params.value,
			)
			return ActionResult(extracted_content=content)

		@self.registry.action('Complete task', param_model=DoneAction, requires_browser=True)
		async def done(params: DoneAction, browser: Browser):
			session = await browser.get_session()
			state = session.cached_state
			return ActionResult(is_done=True, extracted_content=params.text)

		@self.registry.action(
			'Scroll down the page by pixel amount - if no amount is specified, scroll down one page',
			param_model=ScrollAction,
			requires_browser=True,
		)
		async def scroll_down(params: ScrollAction, browser: Browser):
			page = await browser.get_current_page()
			if params.amount is not None:
				await page.evaluate(f'window.scrollBy(0, {params.amount});')
			else:
				await page.keyboard.press('PageDown')

			amount = params.amount if params.amount is not None else 'one page'
			return ActionResult(
				extracted_content=f'Scrolled down the page by {amount} pixels',
				include_in_memory=True,
			)

		# scroll up
		@self.registry.action(
			'Scroll up the page by pixel amount - if no amount is specified, scroll up one page',
			param_model=ScrollAction,
			requires_browser=True,
		)
		async def scroll_up(params: ScrollAction, browser: Browser):
			page = await browser.get_current_page()
			if params.amount is not None:
				await page.evaluate(f'window.scrollBy(0, -{params.amount});')
			else:
				await page.keyboard.press('PageUp')

			amount = params.amount if params.amount is not None else 'one page'
			return ActionResult(
				extracted_content=f'Scrolled up the page by {amount} pixels',
				include_in_memory=True,
			)

	def action(self, description: str, **kwargs):
		"""Decorator for registering custom actions

		@param description: Describe the LLM what the function does (better description == better function calling)
		"""
		return self.registry.action(description, **kwargs)

	@time_execution_sync('--act')
	async def act(self, action: ActionModel) -> ActionResult:
		"""Execute an action"""
		try:
			for action_name, params in action.model_dump(exclude_unset=True).items():
				if params is not None:
					result = await self.registry.execute_action(
						action_name, params, browser=self.browser
					)
					if isinstance(result, str):
						return ActionResult(extracted_content=result)
					elif isinstance(result, ActionResult):
						return result
					elif result is None:
						return ActionResult()
					else:
						raise ValueError(f'Invalid action result type: {type(result)} of {result}')
			return ActionResult()
		except Exception as e:
			raise e
