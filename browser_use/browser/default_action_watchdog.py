"""Default browser action handlers using CDP."""

import asyncio
import os
from typing import TYPE_CHECKING

from cdp_use import (
	CDPClient,
	get_element_box,
)

from browser_use.browser.events import (
	BrowserErrorEvent,
	ClickElementEvent,
	GoBackEvent,
	GoForwardEvent,
	ScrollEvent,
	ScrollToTextEvent,
	SendKeysEvent,
	TypeTextEvent,
	UploadFileEvent,
	WaitEvent,
)
from browser_use.browser.views import BrowserError, URLNotAllowedError
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.logging_config import logger
from browser_use.utils import _log_pretty_url

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession
	from browser_use.dom.service import DOMService


class DefaultActionWatchdog(BaseWatchdog):
	"""Handles default browser actions like click, type, and scroll using CDP."""


	async def on_ClickElementEvent(self, event: ClickElementEvent) -> None:
		"""Handle click request with CDP."""
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
		Click an element using pure CDP.

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

			# Scroll element into view
			try:
				await cdp_client.send.DOM.scrollIntoViewIfNeeded(
					params={'backendNodeId': backend_node_id}, session_id=session_id
				)
				await asyncio.sleep(0.1)  # Wait for scroll to complete
			except Exception as e:
				logger.debug(f'Failed to scroll element into view: {e}')

			# Set up download detection if downloads are enabled
			download_path = None
			download_event = asyncio.Event()
			download_guid = None

			if self.browser_session.browser_profile.downloads_path:
				# Enable download events
				await cdp_client.send.Page.setDownloadBehavior(
					params={'behavior': 'allow', 'downloadPath': str(self.browser_session.browser_profile.downloads_path)},
					session_id=session_id,
				)

				# Set up download listener
				async def on_download_will_begin(event):
					nonlocal download_guid
					download_guid = event['guid']
					download_event.set()

				cdp_client.on('Page.downloadWillBegin', on_download_will_begin, session_id=session_id)  # type: ignore[attr-defined]

			# Perform the click using CDP
			try:
				# Move mouse to element
				await cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mouseMoved',
						'x': center_x,
						'y': center_y,
					},
					session_id=session_id,
				)

				# Mouse down
				await cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mousePressed',
						'x': center_x,
						'y': center_y,
						'button': 'left',
						'clickCount': 1,
					},
					session_id=session_id,
				)

				# Mouse up
				await cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mouseReleased',
						'x': center_x,
						'y': center_y,
						'button': 'left',
						'clickCount': 1,
					},
					session_id=session_id,
				)

				# Handle download if expected
				if self.browser_session.browser_profile.downloads_path:
					try:
						# Wait for download to start (with timeout)
						await asyncio.wait_for(download_event.wait(), timeout=5.0)

						# Wait for download to complete
						download_complete = False
						for _ in range(60):  # Wait up to 60 seconds
							try:
								# Check download progress
								response = await cdp_client.send.Page.getDownloadProgress(
									params={'guid': download_guid}, session_id=session_id
								)
								if response['state'] == 'completed':
									download_complete = True
									break
								elif response['state'] == 'canceled':
									logger.warning('Download was canceled')
									break
							except Exception:
								pass
							await asyncio.sleep(1)

						if download_complete and download_guid:
							logger.info(f'⬇️ Download completed via CDP')
							# Track the download (note: CDP doesn't give us filename directly)
							self.browser_session._downloaded_files.append(f'download_{download_guid}')
							return f'download_{download_guid}'  # Return guid as placeholder
					except asyncio.TimeoutError:
						# No download triggered, normal click
						logger.debug('No download triggered within timeout.')

				# Wait for navigation/changes
				await asyncio.sleep(0.5)
				await self.browser_session._check_and_handle_navigation(page)

				return download_path

			except Exception as e:
				logger.warning(f'CDP click failed: {type(e).__name__}: {e}')
				# Fall back to JavaScript click via CDP
				try:
					result = await cdp_client.send.DOM.resolveNode(
						params={'backendNodeId': backend_node_id},
						session_id=session_id,
					)
					object_id = result['object']['objectId']

					await cdp_client.send.Runtime.callFunctionOn(
						params={
							'functionDeclaration': 'function() { this.click(); }',
							'objectId': object_id,
						},
						session_id=session_id,
					)
					await asyncio.sleep(0.5)
					await self.browser_session._check_and_handle_navigation(page)
					return None
				except Exception as js_e:
					logger.error(f'CDP JavaScript click also failed: {js_e}')
					raise Exception(f'Failed to click element: {e}')

		except URLNotAllowedError as e:
			raise e
		except Exception as e:
			raise Exception(f'Failed to click element: {repr(element_node)}. Error: {str(e)}')

	async def _input_text_element_node_impl(self, element_node, text: str, clear_existing: bool = True):
		"""
		Input text into an element using pure CDP.
		"""
		page = await self.browser_session.get_current_page()

		try:
			# Get CDP client
			cdp_client = await self.browser_session.get_cdp_client()

			# Get the correct session ID for the element's frame
			session_id = await self._get_session_id_for_element(cdp_client, element_node)

			# Get element info
			backend_node_id = element_node.backend_node_id

			# Scroll element into view
			try:
				await cdp_client.send.DOM.scrollIntoViewIfNeeded(
					params={'backendNodeId': backend_node_id}, session_id=session_id
				)
				await asyncio.sleep(0.1)
			except Exception as e:
				logger.debug(f'Failed to scroll element into view: {e}')

			# Get object ID for the element
			result = await cdp_client.send.DOM.resolveNode(
				params={'backendNodeId': backend_node_id},
				session_id=session_id,
			)
			object_id = result['object']['objectId']

			# Clear existing text if requested
			if clear_existing:
				await cdp_client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': 'function() { if (this.value !== undefined) this.value = ""; if (this.textContent !== undefined) this.textContent = ""; }',
						'objectId': object_id,
					},
					session_id=session_id,
				)

			# Focus the element
			await cdp_client.send.DOM.focus(
				params={'backendNodeId': backend_node_id},
				session_id=session_id,
			)

			# Type the text character by character
			for char in text:
				# Send keydown
				await cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'keyDown',
						'text': char,
						'key': char,
					},
					session_id=session_id,
				)
				# Send char (for actual text input)
				await cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'char',
						'text': char,
						'key': char,
					},
					session_id=session_id,
				)
				# Send keyup
				await cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'keyUp',
						'text': char,
						'key': char,
					},
					session_id=session_id,
				)
				# Small delay between characters
				await asyncio.sleep(0.005)

		except Exception as e:
			logger.error(f'Failed to input text via CDP: {type(e).__name__}: {e}')
			raise BrowserError(f'Failed to input text into element: {repr(element_node)}')

	async def _scroll_with_cdp_gesture(self, page, pixels: int) -> bool:
		"""
		Scroll using CDP Input.dispatchMouseEvent to simulate mouse wheel.

		Args:
			page: The page to scroll
			pixels: Number of pixels to scroll (positive = down, negative = up)

		Returns:
			True if successful, False if failed
		"""
		try:
			# Get CDP client and session
			cdp_client = await self.browser_session.get_cdp_client()
			session_id = await self.browser_session.get_current_page_cdp_session_id()

			# Get viewport dimensions
			layout_metrics = await cdp_client.send.Page.getLayoutMetrics(session_id=session_id)
			viewport_width = layout_metrics['layoutViewport']['clientWidth']
			viewport_height = layout_metrics['layoutViewport']['clientHeight']

			# Calculate center of viewport
			center_x = viewport_width / 2
			center_y = viewport_height / 2

			# For mouse wheel, positive deltaY scrolls down, negative scrolls up
			delta_y = pixels

			# Dispatch mouse wheel event
			await cdp_client.send.Input.dispatchMouseEvent(
				params={
					'type': 'mouseWheel',
					'x': center_x,
					'y': center_y,
					'deltaX': 0,
					'deltaY': delta_y,
				},
				session_id=session_id,
			)

			logger.debug(f'📄 Scrolled via CDP mouse wheel: {pixels}px')
			return True

		except Exception as e:
			logger.warning(f'❌ Scrolling via CDP failed: {type(e).__name__}: {e}')
			return False

	async def _scroll_element_container(self, element_node, pixels: int) -> bool:
		"""Try to scroll an element's container using CDP."""
		try:
			# Get CDP client
			cdp_client = await self.browser_session.get_cdp_client()

			# Get the correct session ID for the element's frame
			session_id = await self._get_session_id_for_element(cdp_client, element_node)

			# Get element bounds to know where to scroll
			backend_node_id = element_node.backend_node_id
			box_model = await get_element_box(cdp_client, backend_node_id, session_id=session_id)
			content_quad = box_model['content']

			# Calculate center point
			center_x = (content_quad[0] + content_quad[2] + content_quad[4] + content_quad[6]) / 4
			center_y = (content_quad[1] + content_quad[3] + content_quad[5] + content_quad[7]) / 4

			# Dispatch mouse wheel event at element location
			await cdp_client.send.Input.dispatchMouseEvent(
				params={
					'type': 'mouseWheel',
					'x': center_x,
					'y': center_y,
					'deltaX': 0,
					'deltaY': pixels,
				},
				session_id=session_id,
			)

			return True
		except Exception as e:
			logger.debug(f'Failed to scroll element container via CDP: {e}')
			return False

	async def _get_session_id_for_element(self, cdp_client: CDPClient, element_node) -> str:
		"""Get the appropriate CDP session ID for an element based on its frame."""
		if element_node.frame_id:
			# Element is in an iframe, need to get session for that frame
			try:
				# Get all targets
				targets = await cdp_client.send.Target.getTargets()

				# Find the target for this frame
				for target in targets['targetInfos']:
					if target['type'] == 'iframe' and element_node.frame_id in str(target.get('targetId', '')):
						# Attach to this target
						target_id = target['targetId']
						session = await cdp_client.send.Target.attachToTarget(
							params={'targetId': target_id, 'flatten': True}
						)
						session_id = session['sessionId']

						# Enable required domains on this session
						await cdp_client.send.DOM.enable(session_id=session_id)
						await cdp_client.send.Runtime.enable(session_id=session_id)
						await cdp_client.send.Input.enable(session_id=session_id)

						return session_id

			# If frame not found in targets, use main page session
			logger.debug(f'Frame {element_node.frame_id} not found in targets, using main session')
		except Exception as e:
			logger.debug(f'Error getting frame session: {e}, using main session')

		# Use main page session
		return await self.browser_session.get_current_page_cdp_session_id()
