"""
Playwright browser on steroids.
"""

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TypedDict

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


class BrowserWindowSize(TypedDict):
	width: int
	height: int


@dataclass
class BrowserConfig:
	"""
	Configuration for the Browser.

	Default values:
		headless: False
			Whether to run browser in headless mode

		keep_open: False
			Keep browser open after script finishes

		disable_security: False
			Disable browser security features

		cookies_file: None
			Path to cookies file for persistence

		minimum_wait_page_load_time: 0.5
			Minimum time to wait before getting page state for LLM input

		wait_for_network_idle_page_load_time: 1.0
			Time to wait for network requests to finish before getting page state.
			Lower values may result in incomplete page loads.

		maximum_wait_page_load_time: 5.0
			Maximum time to wait for page load before proceeding anyway
	"""

	headless: bool = False
	keep_open: bool = False
	disable_security: bool = False
	cookies_file: str | None = None
	minimum_wait_page_load_time: float = 0.5
	wait_for_network_idle_page_load_time: float = 1
	maximum_wait_page_load_time: float = 5

	extra_chromium_args: list[str] = field(default_factory=list)
	browser_window_size: BrowserWindowSize = field(
		default_factory=lambda: {'width': 1280, 'height': 1024}
	)


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
	MAXIMUM_WAIT_TIME = 5

	def __init__(
		self,
		config: BrowserConfig,
	):
		self.config = config

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
			if self.config.disable_security:
				disable_security_args = [
					'--disable-web-security',
					'--disable-site-isolation-trials',
					'--disable-features=IsolateOrigins,site-per-process',
				]

			browser = await playwright.chromium.launch(
				headless=self.config.headless,
				args=[
					'--no-sandbox',
					'--disable-blink-features=AutomationControlled',
					'--disable-infobars',
					'--disable-background-timer-throttling',
					'--disable-popup-blocking',
					'--disable-backgrounding-occluded-windows',
					'--disable-renderer-backgrounding',
					'--disable-window-activation',
					'--disable-focus-on-load',
					'--no-first-run',
					'--no-default-browser-check',
					'--no-startup-window',
					'--window-position=0,0',
				]
				+ disable_security_args
				+ self.config.extra_chromium_args,
			)

			return browser
		except Exception as e:
			logger.error(f'Failed to initialize Playwright browser: {str(e)}')
			raise

	async def _create_context(self, browser: PlaywrightBrowser):
		"""Creates a new browser context with anti-detection measures and loads cookies if available."""
		context = await browser.new_context(
			viewport=self.config.browser_window_size,
			user_agent=(
				'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
				'(KHTML, like Gecko) Chrome/85.0.4183.102 Safari/537.36'
			),
			java_script_enabled=True,
			bypass_csp=self.config.disable_security,
			ignore_https_errors=self.config.disable_security,
		)

		# Load cookies if they exist
		if self.config.cookies_file and os.path.exists(self.config.cookies_file):
			with open(self.config.cookies_file, 'r') as f:
				cookies = json.load(f)
				logger.info(f'Loaded {len(cookies)} cookies from {self.config.cookies_file}')
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

	async def _wait_for_stable_network(self):
		page = await self.get_current_page()

		pending_requests = set()
		last_activity = asyncio.get_event_loop().time()

		# Define relevant resource types and content types
		RELEVANT_RESOURCE_TYPES = {'document', 'stylesheet', 'image', 'font', 'script', 'iframe'}

		RELEVANT_CONTENT_TYPES = {
			'text/html',
			'text/css',
			'application/javascript',
			'image/',
			'font/',
			'application/json',
		}

		# Additional patterns to filter out
		IGNORED_URL_PATTERNS = {
			# Analytics and tracking
			'analytics',
			'tracking',
			'telemetry',
			'beacon',
			'metrics',
			# Ad-related
			'doubleclick',
			'adsystem',
			'adserver',
			'advertising',
			# Social media widgets
			'facebook.com/plugins',
			'platform.twitter',
			'linkedin.com/embed',
			# Live chat and support
			'livechat',
			'zendesk',
			'intercom',
			'crisp.chat',
			'hotjar',
			# Push notifications
			'push-notifications',
			'onesignal',
			'pushwoosh',
			# Background sync/heartbeat
			'heartbeat',
			'ping',
			'alive',
			# WebRTC and streaming
			'webrtc',
			'rtmp://',
			'wss://',
			# Common CDNs for dynamic content
			'cloudfront.net',
			'fastly.net',
		}

		async def on_request(request):
			# Filter by resource type
			if request.resource_type not in RELEVANT_RESOURCE_TYPES:
				return

			# Filter out streaming, websocket, and other real-time requests
			if request.resource_type in {'websocket', 'media', 'eventsource', 'manifest', 'other'}:
				return

			# Filter out by URL patterns
			url = request.url.lower()
			if any(pattern in url for pattern in IGNORED_URL_PATTERNS):
				return

			# Filter out data URLs and blob URLs
			if url.startswith(('data:', 'blob:')):
				return

			# Filter out requests with certain headers
			headers = request.headers
			if headers.get('purpose') == 'prefetch' or headers.get('sec-fetch-dest') in [
				'video',
				'audio',
			]:
				return

			nonlocal last_activity
			pending_requests.add(request)
			last_activity = asyncio.get_event_loop().time()
			# logger.debug(f'Request started: {request.url} ({request.resource_type})')

		async def on_response(response):
			request = response.request
			if request not in pending_requests:
				return

			# Filter by content type if available
			content_type = response.headers.get('content-type', '').lower()

			# Skip if content type indicates streaming or real-time data
			if any(
				t in content_type
				for t in [
					'streaming',
					'video',
					'audio',
					'webm',
					'mp4',
					'event-stream',
					'websocket',
					'protobuf',
				]
			):
				pending_requests.remove(request)
				return

			# Only process relevant content types
			if not any(ct in content_type for ct in RELEVANT_CONTENT_TYPES):
				pending_requests.remove(request)
				return

			# Skip if response is too large (likely not essential for page load)
			content_length = response.headers.get('content-length')
			if content_length and int(content_length) > 5 * 1024 * 1024:  # 5MB
				pending_requests.remove(request)
				return

			nonlocal last_activity
			pending_requests.remove(request)
			last_activity = asyncio.get_event_loop().time()
			# logger.debug(f'Request resolved: {request.url} ({content_type})')

		# Attach event listeners
		page.on('request', on_request)
		page.on('response', on_response)

		try:
			# Wait for idle time
			start_time = asyncio.get_event_loop().time()
			while True:
				await asyncio.sleep(0.1)
				now = asyncio.get_event_loop().time()
				if (
					len(pending_requests) == 0
					and (now - last_activity) >= self.config.wait_for_network_idle_page_load_time
				):
					break
				if now - start_time > self.config.maximum_wait_page_load_time:
					logger.debug(
						f'Network timeout after {self.config.maximum_wait_page_load_time}s with {len(pending_requests)} '
						f'pending requests: {[r.url for r in pending_requests]}'
					)
					break

		finally:
			# Clean up event listeners
			page.remove_listener('request', on_request)
			page.remove_listener('response', on_response)

		logger.debug(
			f'Network stabilized for {self.config.wait_for_network_idle_page_load_time} seconds'
		)

	async def _wait_for_page_and_frames_load(self, timeout_overwrite: float | None = None):
		"""
		Ensures page is fully loaded before continuing.
		Waits for either network to be idle or minimum WAIT_TIME, whichever is longer.
		"""
		# Start timing
		start_time = time.time()

		# await asyncio.sleep(self.minimum_wait_page_load_time)

		# Wait for page load
		try:
			await self._wait_for_stable_network()
		except Exception:
			logger.warning('Page load failed, continuing...')
			pass

		# Calculate remaining time to meet minimum WAIT_TIME
		elapsed = time.time() - start_time
		remaining = max((timeout_overwrite or self.config.minimum_wait_page_load_time) - elapsed, 0)

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

		await self.save_cookies()

		if force and not self.config.keep_open:
			session = await self.get_session()
			await session.browser.close()
			await session.playwright.stop()

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

		# Save cookies if a file is specified
		if self.config.cookies_file:
			asyncio.create_task(self.save_cookies())

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
		"""Converts simple XPath expressions to CSS selectors."""
		if not xpath:
			return ''

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
				base_part = part[: part.find('[')]
				index_part = part[part.find('[') :]

				# Handle multiple indices
				indices = [i.strip('[]') for i in index_part.split(']')[:-1]]

				for idx in indices:
					try:
						# Handle numeric indices
						if idx.isdigit():
							index = int(idx) - 1
							base_part += f':nth-of-type({index+1})'
						# Handle last() function
						elif idx == 'last()':
							base_part += ':last-of-type'
						# Handle position() functions
						elif 'position()' in idx:
							if '>1' in idx:
								base_part += ':nth-of-type(n+2)'
					except ValueError:
						continue

				css_parts.append(base_part)
			else:
				css_parts.append(part)

		base_selector = ' > '.join(css_parts)
		return base_selector

	def _enhanced_css_selector_for_element(self, element: DOMElementNode) -> str:
		"""
		Creates a CSS selector for a DOM element, handling various edge cases and special characters.

		Args:
			element: The DOM element to create a selector for

		Returns:
			A valid CSS selector string
		"""
		try:
			# Get base selector from XPath
			css_selector = self._convert_simple_xpath_to_css_selector(element.xpath)

			# Handle class attributes
			if 'class' in element.attributes and element.attributes['class']:
				classes = element.attributes['class'].split()
				for class_name in classes:
					# Skip empty class names
					if not class_name:
						continue

					# Escape special characters in class names
					if any(char in class_name for char in ':()[],>+~|.# '):
						# Use attribute contains for special characters
						css_selector += f'[class*="{class_name}"]'
					else:
						css_selector += f'.{class_name}'

			# Handle other attributes
			for attribute, value in element.attributes.items():
				if attribute == 'class':
					continue

				# Skip invalid attribute names
				if not attribute.strip():
					continue

				# Escape special characters in attribute names
				safe_attribute = attribute.replace(':', r'\:')

				# Handle different value cases
				if value == '':
					css_selector += f'[{safe_attribute}]'
				elif any(char in value for char in '"\'<>`'):
					# Use contains for values with special characters
					safe_value = value.replace('"', '\\"')
					css_selector += f'[{safe_attribute}*="{safe_value}"]'
				else:
					css_selector += f'[{safe_attribute}="{value}"]'

			return css_selector

		except Exception:
			# Fallback to a more basic selector if something goes wrong
			tag_name = element.tag_name or '*'
			return f"{tag_name}[highlight_index='{element.highlight_index}']"

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
		if self.session and self.session.context and self.config.cookies_file:
			try:
				cookies = await self.session.context.cookies()
				logger.info(f'Saving {len(cookies)} cookies to {self.config.cookies_file}')

				# Check if the path is a directory and create it if necessary
				dirname = os.path.dirname(self.config.cookies_file)
				if dirname:
					os.makedirs(dirname, exist_ok=True)

				with open(self.config.cookies_file, 'w') as f:
					json.dump(cookies, f)
			except Exception as e:
				logger.warning(f'Failed to save cookies: {str(e)}')
