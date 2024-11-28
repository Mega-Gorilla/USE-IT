"""
Playwright browser on steroids.
"""

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import (
	BrowserContext,
	ElementHandle,
	FrameLocator,
	Page,
	Playwright,
	async_playwright,
)

from browser_use.browser.views import BrowserError, BrowserState, TabInfo
from browser_use.dom.service import DomService
from browser_use.dom.views import DOMElementNode, SelectorMap
from browser_use.utils import time_execution_sync

logger = logging.getLogger(__name__)


@dataclass
class BrowserSession:
	playwright: Playwright
	browser: PlaywrightBrowser
	context: BrowserContext
	current_page: Page
	cached_state: BrowserState
	# current_page_id: str
	# opened_tabs: dict[str, TabInfo] = field(default_factory=dict)


class Browser:
	MINIMUM_WAIT_TIME = 0.5
	MAXIMUM_WAIT_TIME = 5

	def __init__(
		self,
		headless: bool = False,
		keep_open: bool = False,
		cookies_path: str | None = None,
		disable_security: bool = False,
	):
		self.headless = headless
		self.keep_open = keep_open
		self.cookies_file = cookies_path
		self.disable_security = disable_security
		# Initialize these as None - they'll be set up when needed
		self.session: BrowserSession | None = None

	async def _initialize_session(self):
		"""Initialize the browser session"""
		playwright = await async_playwright().start()
		browser = await self._setup_browser(playwright)
		context = await self._create_context(browser)
		page = await context.new_page()

		# Instead of calling _update_state(), create an empty initial state
		initial_state = BrowserState(
			element_tree=DOMElementNode(
				tag_name='root', is_visible=True, parent=None, xpath='', attributes={}, children=[]
			),
			selector_map={},
			url=page.url,
			title=await page.title(),
			screenshot=None,
			tabs=[],
			interacted_element=None,
		)

		self.session = BrowserSession(
			playwright=playwright,
			browser=browser,
			context=context,
			current_page=page,
			cached_state=initial_state,
		)

		await self._add_new_page_listener(context)

		return self.session

	async def _add_new_page_listener(self, context: BrowserContext):
		async def on_page(page: Page):
			await page.wait_for_load_state()
			logger.debug(f'New page opened: {page.url}')
			if self.session is not None:
				self.session.current_page = page

		context.on('page', on_page)

	async def get_session(self) -> BrowserSession:
		"""Lazy initialization of the browser and related components"""
		if self.session is None:
			return await self._initialize_session()
		return self.session

	async def get_current_page(self) -> Page:
		"""Get the current page"""
		session = await self.get_session()
		return session.current_page

	async def _setup_browser(self, playwright: Playwright) -> PlaywrightBrowser:
		"""Sets up and returns a Playwright Browser instance with anti-detection measures."""
		try:
			disable_security_args = []
			if self.disable_security:
				disable_security_args = [
					'--disable-web-security',
					'--disable-site-isolation-trials',
					'--disable-features=IsolateOrigins,site-per-process',
				]
			browser = await playwright.chromium.launch(
				headless=self.headless,
				ignore_default_args=['--enable-automation'],  # Helps with anti-detection
				args=[
					'--no-sandbox',
					'--disable-blink-features=AutomationControlled',
					'--disable-extensions',
					'--disable-infobars',
					'--disable-background-timer-throttling',
					'--disable-popup-blocking',
					'--disable-backgrounding-occluded-windows',
					'--disable-renderer-backgrounding',
					'--disable-window-activation',
					'--disable-focus-on-load',  # Prevents focus on navigation
					'--no-first-run',
					'--no-default-browser-check',
					'--no-startup-window',  # Prevents initial focus
					'--window-position=0,0',
				]
				+ disable_security_args,
			)

			return browser
		except Exception as e:
			logger.error(f'Failed to initialize Playwright browser: {str(e)}')
			raise

	async def _create_context(self, browser: PlaywrightBrowser):
		"""Creates a new browser context with anti-detection measures and loads cookies if available."""
		context = await browser.new_context(
			viewport={'width': 1280, 'height': 1024},
			user_agent=(
				'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
				'(KHTML, like Gecko) Chrome/85.0.4183.102 Safari/537.36'
			),
			java_script_enabled=True,
			bypass_csp=self.disable_security,
			ignore_https_errors=self.disable_security,
		)

		# Load cookies if they exist
		if self.cookies_file and os.path.exists(self.cookies_file):
			with open(self.cookies_file, 'r') as f:
				cookies = json.load(f)
				logger.info(f'Loaded {len(cookies)} cookies from {self.cookies_file}')
				await context.add_cookies(cookies)

		# Expose anti-detection scripts
		await context.add_init_script(
			"""
			// Webdriver property
			Object.defineProperty(navigator, 'webdriver', {
				get: () => undefined
			});

			// Languages
			Object.defineProperty(navigator, 'languages', {
				get: () => ['en-US', 'en']
			});

			// Plugins
			Object.defineProperty(navigator, 'plugins', {
				get: () => [1, 2, 3, 4, 5]
			});

			// Chrome runtime
			window.chrome = { runtime: {} };

			// Permissions
			const originalQuery = window.navigator.permissions.query;
			window.navigator.permissions.query = (parameters) => (
				parameters.name === 'notifications' ?
					Promise.resolve({ state: Notification.permission }) :
					originalQuery(parameters)
			);
			"""
		)

		return context

	async def _wait_for_page_and_frames_load(self, timeout_overwrite: float | None = None):
		"""
		Ensures page is fully loaded before continuing.
		Waits for either document.readyState to be complete or minimum WAIT_TIME, whichever is longer.
		"""
		page = await self.get_current_page()

		# Start timing
		start_time = time.time()

		# Wait for page load
		try:
			await page.wait_for_load_state('domcontentloaded', timeout=5000)
		except Exception:
			logger.warning('Page load failed, continuing...')
			pass

		# Calculate remaining time to meet minimum WAIT_TIME
		elapsed = time.time() - start_time
		remaining = max((timeout_overwrite or self.MINIMUM_WAIT_TIME) - elapsed, 0)

		logger.debug(
			f'--Page loaded in {elapsed:.2f} seconds, waiting for additional {remaining:.2f} seconds'
		)

		# Sleep remaining time if needed
		if remaining > 0:
			await asyncio.sleep(remaining)

	async def close(self, force: bool = False):
		"""Close the browser instance"""

		# check if already closed
		if self.session is None:
			return

		if self.cookies_file:
			await self.save_cookies()

		if force and not self.keep_open:
			session = await self.get_session()
			await session.browser.close()
			await session.playwright.stop()

		else:
			# Note: input() is blocking - consider an async alternative if needed
			input('Press Enter to close Browser...')
			self.keep_open = False
			await self.close(force=True)

	def __del__(self):
		"""Async cleanup when object is destroyed"""
		if self.session is not None:
			asyncio.run(self.close(force=True))

	async def navigate_to(self, url: str):
		"""Navigate to a URL"""
		page = await self.get_current_page()
		await page.goto(url)
		await page.wait_for_load_state()

	async def refresh_page(self):
		"""Refresh the current page"""
		page = await self.get_current_page()
		await page.reload()
		await page.wait_for_load_state()

	async def go_back(self):
		"""Navigate back in history"""
		page = await self.get_current_page()
		await page.go_back()
		await page.wait_for_load_state()

	async def go_forward(self):
		"""Navigate forward in history"""
		page = await self.get_current_page()
		await page.go_forward()
		await page.wait_for_load_state()

	async def close_current_tab(self):
		"""Close the current tab"""
		session = await self.get_session()
		page = session.current_page
		await page.close()

		# Switch to the first available tab if any exist
		if session.context.pages:
			await self.switch_to_tab(0)

		# otherwise the browser will be closed

	async def get_page_html(self) -> str:
		"""Get the current page HTML content"""
		page = await self.get_current_page()
		return await page.content()

	async def execute_javascript(self, script: str):
		"""Execute JavaScript code on the page"""
		page = await self.get_current_page()
		return await page.evaluate(script)

	@time_execution_sync('--get_state')  # This decorator might need to be updated to handle async
	async def get_state(self, use_vision: bool = False) -> BrowserState:
		"""Get the current state of the browser"""
		await self._wait_for_page_and_frames_load()
		session = await self.get_session()
		session.cached_state = await self._update_state(use_vision=use_vision)
		return session.cached_state

	async def _update_state(self, use_vision: bool = False) -> BrowserState:
		"""Update and return state."""
		await self.remove_highlights()
		page = await self.get_current_page()
		dom_service = DomService(page)
		content = await dom_service.get_clickable_elements()  # Assuming this is async

		screenshot_b64 = None
		if use_vision:
			screenshot_b64 = await self.take_screenshot()

		self.current_state = BrowserState(
			element_tree=content.element_tree,
			selector_map=content.selector_map,
			url=page.url,
			title=await page.title(),
			tabs=await self.get_tabs_info(),
			screenshot=screenshot_b64,
			interacted_element=content.interacted_element,
		)

		return self.current_state

	# region - Browser Actions

	async def take_screenshot(self, full_page: bool = False) -> str:
		"""
		Returns a base64 encoded screenshot of the current page.
		"""
		page = await self.get_current_page()

		screenshot = await page.screenshot(
			full_page=full_page,
			animations='disabled',
		)

		screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')

		await self.remove_highlights()

		return screenshot_b64

	async def remove_highlights(self):
		"""
		Removes all highlight overlays and labels created by the highlightElement function.
		"""
		page = await self.get_current_page()
		await page.evaluate(
			"""
			// Remove the highlight container and all its contents
			const container = document.getElementById('playwright-highlight-container');
			if (container) {
				container.remove();
			}

			// Remove highlight attributes from elements
			const highlightedElements = document.querySelectorAll('[browser-user-highlight-id^="playwright-highlight-"]');
			highlightedElements.forEach(el => {
				el.removeAttribute('browser-user-highlight-id');
			});
			"""
		)

	# endregion

	# region - User Actions
	def _convert_simple_xpath_to_css_selector(self, xpath: str) -> str:
		"""Converts simple XPath expressions to CSS selectors.
		Only handles basic XPath patterns like tag names, indices, and attributes.
		"""
		# Remove leading slash if present
		xpath = xpath.lstrip('/')

		# Split into parts
		parts = xpath.split('/')

		css_parts = []
		for part in parts:
			if not part:
				continue

			# Handle index notation [n]
			if '[' in part:
				tag = part[: part.find('[')]
				index = int(part[part.find('[') + 1 : part.find(']')]) - 1
				css_parts.append(f'{tag}:nth-of-type({index+1})')
			else:
				css_parts.append(part)

		return ' > '.join(css_parts)

	def _enhanced_css_selector_for_element(self, element: DOMElementNode) -> str:
		css_selector = self._convert_simple_xpath_to_css_selector(element.xpath)

		# Handle class attributes first
		if 'class' in element.attributes:
			classes = element.attributes['class'].split()
			css_selector += '.' + '.'.join(classes)

		# Then add all other attribute selectors
		for attribute, value in element.attributes.items():
			if attribute != 'class':  # Skip class since we already handled it
				css_selector += f'[{attribute}="{value}"]'

		return css_selector

	async def get_locate_element(self, element: DOMElementNode):
		current_frame = await self.get_current_page()

		# Start with the target element and collect all parents
		parents: list[DOMElementNode] = []
		current = element
		while current.parent is not None:
			parent = current.parent
			parents.append(parent)
			current = parent
			if parent.tag_name == 'iframe':
				break

		# there can be only one iframe parent (by design of the loop above)
		iframe_parent = [item for item in parents if item.tag_name == 'iframe']
		if iframe_parent:
			parent = iframe_parent[0]
			css_selector = self._enhanced_css_selector_for_element(parent)
			current_frame = current_frame.frame_locator(css_selector)

		css_selector = self._enhanced_css_selector_for_element(element)

		if isinstance(current_frame, FrameLocator):
			return await current_frame.locator(css_selector).element_handle()
		else:
			return await current_frame.wait_for_selector(
				css_selector, timeout=5000, state='visible'
			)

	async def _input_text_element_node(self, element_node: DOMElementNode, text: str):
		try:
			page = await self.get_current_page()
			element = await self.get_locate_element(element_node)

			if element is None:
				raise Exception(f'Element: {repr(element_node)} not found')

			await element.scroll_into_view_if_needed(timeout=2500)
			await element.fill('')
			await element.type(text)
			await page.wait_for_load_state()

		except Exception as e:
			raise Exception(
				f'Failed to input text into element: {repr(element_node)}. Error: {str(e)}'
			)

	async def _click_element_node(self, element_node: DOMElementNode):
		"""
		Optimized method to click an element using xpath.
		"""
		page = await self.get_current_page()

		try:
			element = await self.get_locate_element(element_node)

			if element is None:
				raise Exception(f'Element: {repr(element_node)} not found')

			# await element.scroll_into_view_if_needed()

			try:
				await element.click(timeout=2500)
				await page.wait_for_load_state()
			except Exception:
				try:
					await page.evaluate('(el) => el.click()', element)
					await page.wait_for_load_state()
				except Exception as e:
					raise Exception(f'Failed to click element: {str(e)}')

		except Exception as e:
			raise Exception(f'Failed to click element: {repr(element_node)}. Error: {str(e)}')

	async def get_tabs_info(self) -> list[TabInfo]:
		"""Get information about all tabs"""
		session = await self.get_session()

		tabs_info = []
		for page_id, page in enumerate(session.context.pages):
			tab_info = TabInfo(page_id=page_id, url=page.url, title=await page.title())
			tabs_info.append(tab_info)

		return tabs_info

	async def switch_to_tab(self, page_id: int) -> None:
		"""Switch to a specific tab by its page_id

		@You can also use negative indices to switch to tabs from the end (Pure pythonic way)
		"""
		session = await self.get_session()
		pages = session.context.pages

		if page_id >= len(pages):
			raise BrowserError(f'No tab found with page_id: {page_id}')

		page = pages[page_id]
		session.current_page = page

		await page.bring_to_front()
		await page.wait_for_load_state()

	async def create_new_tab(self, url: str | None = None) -> None:
		"""Create a new tab and optionally navigate to a URL"""
		session = await self.get_session()
		new_page = await session.context.new_page()
		session.current_page = new_page

		await new_page.wait_for_load_state()

		page = await self.get_current_page()

		if url:
			await page.goto(url)
			await self._wait_for_page_and_frames_load(timeout_overwrite=1)

	# endregion

	# region - Helper methods for easier access to the DOM
	async def get_selector_map(self) -> SelectorMap:
		session = await self.get_session()
		return session.cached_state.selector_map

	async def get_element_by_index(self, index: int) -> ElementHandle | None:
		selector_map = await self.get_selector_map()
		return await self.get_locate_element(selector_map[index])

	async def save_cookies(self):
		"""Save current cookies to file"""
		if self.session and self.session.context and self.cookies_file:
			try:
				cookies = await self.session.context.cookies()
				logger.info(f'Saving {len(cookies)} cookies to {self.cookies_file}')

				# Check if the path is a directory and create it if necessary
				dirname = os.path.dirname(self.cookies_file)
				if dirname:
					os.makedirs(dirname, exist_ok=True)

				with open(self.cookies_file, 'w') as f:
					json.dump(cookies, f)
			except Exception as e:
				logger.error(f'Failed to save cookies: {str(e)}')
