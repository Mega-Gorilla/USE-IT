"""Default browser action handlers using CDP."""

import asyncio
import os
from typing import TYPE_CHECKING

from cdp_use import (
	get_element_box,
)

from browser_use.browser.events import (
	BrowserErrorEvent,
	ClickElementEvent,
	ScrollEvent,
	TypeTextEvent,
)
from browser_use.browser.views import BrowserError, URLNotAllowedError
from browser_use.browser.watchdog_base import BrowserWatchdog
from browser_use.logging_config import logger
from browser_use.utils import _log_pretty_url

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession
	from browser_use.dom.service import DOMService


class DefaultActionWatchdog(BrowserWatchdog):
	"""Handles default browser actions like click, type, and scroll using CDP."""

	def __init__(self, browser_session: 'BrowserSession', dom_service: 'DOMService'):
		super().__init__(browser_session, dom_service)

	def register_event_handlers(self) -> None:
		"""Register handlers for browser action events."""
		self.event_bus.on(ClickElementEvent, self.on_ClickElementEvent)
		self.event_bus.on(TypeTextEvent, self.on_TypeTextEvent)
		self.event_bus.on(ScrollEvent, self.on_ScrollEvent)

	async def on_ClickElementEvent(self, event: ClickElementEvent) -> None:
		"""Handle click request with CDP fallbacks."""
		page = await self.browser_session.get_current_page()
		try:
			# Get the DOM element by index
			element_node = await self.browser_session.get_dom_element_by_index(event.index)
			if element_node is None:
				raise Exception(f'Element index {event.index} does not exist - retry or use alternative actions')

			# Track initial number of tabs to detect new tab opening
			initial_pages = len(self.browser_session.pages)

			# Check if element is a file input (should not be clicked)
			if self.browser_session.is_file_input(element_node):
				msg = f'Index {event.index} - has an element which opens file upload dialog. To upload files please use a specific function to upload files'
				logger.info(msg)
				self.event_bus.dispatch(
					BrowserErrorEvent(
						error_type='FileInputElement',
						message=msg,
						details={'index': event.index},
					)
				)
				return

			# Perform the actual click using internal implementation
			download_path = await self._click_element_node_impl(
				element_node, expect_download=event.expect_download, new_tab=event.new_tab
			)

			# Build success message
			if download_path:
				msg = f'Downloaded file to {download_path}'
				logger.info(f'💾 {msg}')
			else:
				msg = f'Clicked button with index {event.index}: {element_node.get_all_text_till_next_clickable_element(max_depth=2)}'
				logger.info(f'🖱️ {msg}')
			logger.debug(f'Element xpath: {element_node.xpath}')

			# Check if a new tab was opened
			if len(self.browser_session.pages) > initial_pages:
				new_tab_msg = 'New tab opened - switching to it'
				msg += f' - {new_tab_msg}'
				logger.info(f'🔗 {new_tab_msg}')
				# Switch to the last tab (newly created tab)
				last_tab_index = len(self.browser_session.pages) - 1
				await self.browser_session.switch_to_tab(last_tab_index)
		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='ClickFailed',
					message=str(e),
					details={'index': event.index},
				)
			)

	async def on_TypeTextEvent(self, event: TypeTextEvent) -> None:
		"""Handle text input request with CDP."""
		page = await self.browser_session.get_current_page()
		try:
			# Get the DOM element by index
			element_node = await self.browser_session.get_dom_element_by_index(event.index)
			if element_node is None:
				raise Exception(f'Element index {event.index} does not exist - retry or use alternative actions')

			# Perform the actual text input
			await self._input_text_element_node_impl(element_node, event.text, event.clear_existing)

			# Log success
			logger.info(f'⌨️ Typed "{event.text}" into element with index {event.index}')
			logger.debug(f'Element xpath: {element_node.xpath}')
		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='InputTextFailed',
					message=str(e),
					details={'index': event.index, 'text': event.text},
				)
			)

	async def on_ScrollEvent(self, event: ScrollEvent) -> None:
		"""Handle scroll request with CDP."""
		try:
			page = await self.browser_session.get_current_page()
		except ValueError:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='NoActivePage',
					message='No active page for scrolling',
				)
			)
			return

		try:
			# Convert direction and amount to pixels
			# Positive pixels = scroll down, negative = scroll up
			pixels = event.amount if event.direction == 'down' else -event.amount

			# Element-specific scrolling if index is provided
			if event.element_index is not None:
				element_node = await self.browser_session.get_dom_element_by_index(event.element_index)
				if element_node is None:
					raise Exception(f'Element index {event.element_index} does not exist')

				# Try to scroll the element's container
				success = await self._scroll_element_container(element_node, pixels)
				if success:
					logger.info(f'📜 Scrolled element {event.element_index} container {event.direction} by {event.amount} pixels')
					return

			# Perform page-level scroll
			await self._scroll_with_cdp_gesture(page, pixels)

			# Log success
			logger.info(f'📜 Scrolled {event.direction} by {event.amount} pixels')
		except Exception as e:
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='ScrollFailed',
					message=str(e),
					details={'direction': event.direction, 'amount': event.amount},
				)
			)

	# ========== Implementation Methods ==========

	async def _click_element_node_impl(self, element_node, expect_download: bool = False, new_tab: bool = False) -> str | None:
		"""
		Optimized method to click an element using pure CDP.

		Args:
			element_node: The DOM element to click
			expect_download: If True, wait for download and handle it inline
			new_tab: If True, open any resulting navigation in a new tab

		Returns:
			The download path if a download was triggered, None otherwise
		"""
		page = await self.browser_session.get_current_page()

		try:
			# Get CDP client
			cdp_client = await self.browser_session.get_cdp_client()

			# Get the correct session ID for the element's frame
			session_id = await self._get_session_id_for_element(cdp_client, element_node)

			# Get element bounds
			backend_node_id = element_node.backend_node_id

			# Get bounds from CDP
			box_model = await get_element_box(cdp_client, backend_node_id, session_id=session_id)
			content_quad = box_model['content']
			if len(content_quad) < 8:
				raise Exception('Invalid content quad')

			# Calculate center point from quad
			center_x = (content_quad[0] + content_quad[2] + content_quad[4] + content_quad[6]) / 4
			center_y = (content_quad[1] + content_quad[3] + content_quad[5] + content_quad[7]) / 4

			async def perform_click(click_func):
				"""Performs the actual click, handling both download and navigation scenarios."""

				# only wait the 5s extra for potential downloads if they are enabled
				if self.browser_session.browser_profile.downloads_path:
					try:
						# Try short-timeout expect_download to detect a file download has been been triggered
						async with page.expect_download(timeout=5_000) as download_info:
							await click_func()
						download = await download_info.value
						# Determine file path
						suggested_filename = download.suggested_filename
						unique_filename = await self.browser_session._get_unique_filename(
							self.browser_session.browser_profile.downloads_path, suggested_filename
						)
						download_path = os.path.join(self.browser_session.browser_profile.downloads_path, unique_filename)
						await download.save_as(download_path)
						logger.info(f'⬇️ Downloaded file to: {download_path}')

						# Track the downloaded file in the session
						self.browser_session._downloaded_files.append(download_path)
						logger.info(
							f'📁 Added download to session tracking (total: {len(self.browser_session._downloaded_files)} files)'
						)

						return download_path
					except Exception:
						# If no download is triggered, treat as normal click
						logger.debug('No download triggered within timeout. Checking navigation...')
						try:
							await page.wait_for_load_state()
						except Exception as e:
							logger.warning(
								f'⚠️ Page {_log_pretty_url(page.url)} failed to finish loading after click: {type(e).__name__}: {e}'
							)
						await self.browser_session._check_and_handle_navigation(page)
				else:
					# If downloads are disabled, just perform the click
					await click_func()
					try:
						await page.wait_for_load_state()
					except Exception as e:
						logger.warning(
							f'⚠️ Page {_log_pretty_url(page.url)} failed to finish loading after click: {type(e).__name__}: {e}'
						)
					await self.browser_session._check_and_handle_navigation(page)

			try:
				return await perform_click(lambda: element_handle and element_handle.click(timeout=1_500))
			except URLNotAllowedError as e:
				raise e
			except Exception as e:
				# Check if it's a context error and provide more info
				if 'Cannot find context with specified id' in str(e) or 'Protocol error' in str(e):
					logger.warning(f'⚠️ Element context lost, attempting to re-locate element: {type(e).__name__}')
					# Try to re-locate the element
					element_handle = await self.browser_session.get_locate_element(element_node)
					if element_handle is None:
						raise Exception(f'Element no longer exists in DOM after context loss: {repr(element_node)}')
					# Try click again with fresh element
					try:
						return await perform_click(lambda: element_handle.click(timeout=1_500))
					except Exception:
						# Fall back to JavaScript click
						return await perform_click(lambda: page.evaluate('(el) => el.click()', element_handle))
				else:
					# Original fallback for other errors
					try:
						return await perform_click(lambda: page.evaluate('(el) => el.click()', element_handle))
					except URLNotAllowedError as e:
						raise e
					except Exception as e:
						# Final fallback - try clicking by coordinates if available
						if element_node.snapshot_node and element_node.snapshot_node.bounds:
							try:
								logger.warning(
									f'⚠️ Element click failed, falling back to coordinate click at ({element_node.snapshot_node.bounds.center})'
								)
								await page.mouse.click(
									element_node.snapshot_node.bounds.center[0],
									element_node.snapshot_node.bounds.center[1],
								)
								try:
									await page.wait_for_load_state()
								except Exception:
									pass
								await self.browser_session._check_and_handle_navigation(page)
								return None  # Success
							except Exception as coord_e:
								logger.error(f'Coordinate click also failed: {type(coord_e).__name__}: {coord_e}')
						raise Exception(f'Failed to click element: {type(e).__name__}: {e}')

		except URLNotAllowedError as e:
			raise e
		except Exception as e:
			raise Exception(f'Failed to click element: {repr(element_node)}. Error: {str(e)}')

	async def _input_text_element_node_impl(self, element_node, text: str, clear_existing: bool = True):
		"""
		Input text into an element with proper error handling and state management.
		Handles different types of input fields and ensures proper element state before input.
		"""
		try:
			element_handle = await self.browser_session.get_locate_element(element_node)

			if element_handle is None:
				raise BrowserError(f'Element: {repr(element_node)} not found')

			# Ensure element is ready for input
			try:
				await element_handle.wait_for_element_state('stable', timeout=1_000)
				is_visible = await self.browser_session._is_visible(element_handle)
				if is_visible:
					await element_handle.scroll_into_view_if_needed(timeout=1_000)
			except Exception:
				pass

			# let's first try to click and type
			try:
				if clear_existing:
					await element_handle.evaluate('el => {el.textContent = ""; el.value = "";}')
				await element_handle.click()
				await asyncio.sleep(0.1)
				page = await self.browser_session.get_current_page()
				await page.keyboard.type(text)
				return
			except Exception as e:
				logger.debug(f'Input text with click and type failed, trying element handle method: {e}')
				pass

			# Get element properties to determine input method
			tag_handle = await element_handle.get_property('tagName')
			tag_name = (await tag_handle.json_value()).lower()
			is_contenteditable = await element_handle.get_property('isContentEditable')
			readonly_handle = await element_handle.get_property('readOnly')
			disabled_handle = await element_handle.get_property('disabled')

			readonly = await readonly_handle.json_value() if readonly_handle else False
			disabled = await disabled_handle.json_value() if disabled_handle else False

			try:
				if (await is_contenteditable.json_value() or tag_name == 'input') and not (readonly or disabled):
					if clear_existing:
						await element_handle.evaluate('el => {el.textContent = ""; el.value = "";}')
					await element_handle.type(text, delay=5)
				else:
					await element_handle.fill(text)
			except Exception as e:
				logger.error(f'Error during input text into element: {type(e).__name__}: {e}')
				raise BrowserError(f'Failed to input text into element: {repr(element_node)}')

		except Exception as e:
			# Get current page URL safely for error message
			try:
				page = await self.browser_session.get_current_page()
				page_url = _log_pretty_url(page.url)
			except Exception:
				page_url = 'unknown page'

			logger.debug(
				f'❌ Failed to input text into element: {repr(element_node)} on page {page_url}: {type(e).__name__}: {e}'
			)
			raise BrowserError(f'Failed to input text into element: {repr(element_node)}')

	async def _scroll_with_cdp_gesture(self, page, pixels: int) -> bool:
		"""
		Scroll using CDP Input.synthesizeScrollGesture for universal compatibility.

		Args:
			page: The page to scroll
			pixels: Number of pixels to scroll (positive = down, negative = up)

		Returns:
			True if successful, False if failed
		"""
		try:
			# Use CDP to synthesize scroll gesture - works in all contexts including PDFs
			cdp_session = await page.context.new_cdp_session(page)  # type: ignore

			# Get viewport center for scroll origin
			viewport = await page.evaluate("""
				() => ({
					width: window.innerWidth,
					height: window.innerHeight
				})
			""")

			center_x = viewport['width'] // 2
			center_y = viewport['height'] // 2

			await cdp_session.send(
				'Input.synthesizeScrollGesture',
				{
					'x': center_x,
					'y': center_y,
					'xDistance': 0,
					'yDistance': -pixels,  # Negative = scroll down, Positive = scroll up
					'gestureSourceType': 'mouse',  # Use mouse gestures for better compatibility
					'speed': 3000,  # Pixels per second
				},
			)

			try:
				await asyncio.wait_for(cdp_session.detach(), timeout=1.0)
			except (TimeoutError, Exception):
				pass
			logger.debug(f'📄 Scrolled via CDP Input.synthesizeScrollGesture: {pixels}px')
			return True

		except Exception as e:
			logger.warning(f'❌ Scrolling via CDP Input.synthesizeScrollGesture failed: {type(e).__name__}: {e}')
			# Fallback to JavaScript
			await self._scroll_container_js(page, pixels)
			return True

	async def _scroll_container_js(self, page, pixels: int) -> None:
		"""Scroll using JavaScript with smart container detection."""
		SMART_SCROLL_JS = """(dy) => {
			const bigEnough = el => el.clientHeight >= window.innerHeight * 0.5;
			const canScroll = el =>
				el &&
				/(auto|scroll|overlay)/.test(getComputedStyle(el).overflowY) &&
				el.scrollHeight > el.clientHeight &&
				bigEnough(el);

			let el = document.activeElement;
			while (el && !canScroll(el) && el !== document.body) el = el.parentElement;

			el = canScroll(el)
					? el
					: [...document.querySelectorAll('*')].find(canScroll)
					|| document.scrollingElement
					|| document.documentElement;

			if (el === document.scrollingElement ||
				el === document.documentElement ||
				el === document.body) {
				window.scrollBy(0, dy);
			} else {
				el.scrollBy({ top: dy, behavior: 'auto' });
			}
		}"""
		await page.evaluate(SMART_SCROLL_JS, pixels)

	async def _scroll_element_container(self, element_node, pixels: int) -> bool:
		"""Try to scroll an element's container."""
		page = await self.browser_session.get_current_page()

		container_scroll_js = """
		(params) => {
			const { dy, elementXPath } = params;
			
			// Get the target element by XPath
			const targetElement = document.evaluate(elementXPath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
			if (!targetElement) {
				return { success: false, reason: 'Element not found by XPath' };
			}

			// Try to find scrollable containers in the hierarchy
			let currentElement = targetElement;
			let scrollSuccess = false;
			let attempts = 0;
			
			while (currentElement && attempts < 10) {
				const computedStyle = window.getComputedStyle(currentElement);
				const hasScrollableY = /(auto|scroll|overlay)/.test(computedStyle.overflowY);
				const canScrollVertically = currentElement.scrollHeight > currentElement.clientHeight;
				
				if (hasScrollableY && canScrollVertically) {
					const beforeScroll = currentElement.scrollTop;
					currentElement.scrollTop = beforeScroll + dy;
					const afterScroll = currentElement.scrollTop;
					
					if (Math.abs(afterScroll - beforeScroll) > 0.5) {
						return { success: true };
					}
				}
				
				currentElement = currentElement.parentElement;
				attempts++;
			}
			
			return { success: false, reason: 'No scrollable container found' };
		}
		"""

		try:
			result = await page.evaluate(container_scroll_js, {'dy': pixels, 'elementXPath': element_node.xpath})
			return result['success']
		except Exception:
			return False
