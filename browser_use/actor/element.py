"""Element class for element operations."""

import asyncio
from typing import TYPE_CHECKING, Literal, Union

from cdp_use.client import logger
from typing_extensions import TypedDict

if TYPE_CHECKING:
	from cdp_use.cdp.dom.commands import (
		DescribeNodeParameters,
		FocusParameters,
		GetAttributesParameters,
		GetBoxModelParameters,
		PushNodesByBackendIdsToFrontendParameters,
		RequestChildNodesParameters,
		ResolveNodeParameters,
	)
	from cdp_use.cdp.input.commands import (
		DispatchMouseEventParameters,
	)
	from cdp_use.cdp.input.types import MouseButton
	from cdp_use.cdp.page.commands import CaptureScreenshotParameters
	from cdp_use.cdp.page.types import Viewport

	from browser_use.browser.session import BrowserSession

# Type definitions for element operations
ModifierType = Literal['Alt', 'Control', 'Meta', 'Shift']


class Position(TypedDict):
	"""2D position coordinates."""

	x: float
	y: float


class BoundingBox(TypedDict):
	"""Element bounding box with position and dimensions."""

	x: float
	y: float
	width: float
	height: float


class ElementInfo(TypedDict):
	"""Basic information about a DOM element."""

	backendNodeId: int
	nodeId: int | None
	nodeName: str
	nodeType: int
	nodeValue: str | None
	attributes: dict[str, str]
	boundingBox: BoundingBox | None
	error: str | None


class Element:
	"""Element operations using BackendNodeId."""

	def __init__(
		self,
		browser_session: 'BrowserSession',
		backend_node_id: int,
		session_id: str | None = None,
	):
		self._browser_session = browser_session
		self._client = browser_session.cdp_client
		self._backend_node_id = backend_node_id
		self._session_id = session_id

	async def _get_node_id(self) -> int:
		"""Get DOM node ID from backend node ID."""
		params: 'PushNodesByBackendIdsToFrontendParameters' = {'backendNodeIds': [self._backend_node_id]}
		result = await self._client.send.DOM.pushNodesByBackendIdsToFrontend(params, session_id=self._session_id)
		return result['nodeIds'][0]

	async def _get_remote_object_id(self) -> str | None:
		"""Get remote object ID for this element."""
		node_id = await self._get_node_id()
		params: 'ResolveNodeParameters' = {'nodeId': node_id}
		result = await self._client.send.DOM.resolveNode(params, session_id=self._session_id)
		object_id = result['object'].get('objectId', None)

		if not object_id:
			return None
		return object_id

	async def click(
		self,
		button: 'MouseButton' = 'left',
		click_count: int = 1,
		modifiers: list[ModifierType] | None = None,
	) -> None:
		"""Click the element using robust fallback strategies."""
		# Try multiple methods to get element geometry
		quads = []

		# Method 1: Try DOM.getContentQuads first (best for inline elements)
		try:
			content_quads_result = await self._client.send.DOM.getContentQuads(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			if 'quads' in content_quads_result and content_quads_result['quads']:
				quads = content_quads_result['quads']
		except Exception:
			pass

		# Method 2: Fall back to DOM.getBoxModel
		if not quads:
			try:
				box_model = await self._client.send.DOM.getBoxModel(
					params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
				)
				if 'model' in box_model and 'content' in box_model['model']:
					content_quad = box_model['model']['content']
					if len(content_quad) >= 8:
						quads = [content_quad]
			except Exception:
				pass

		# Method 3: Fall back to JavaScript getBoundingClientRect
		if not quads:
			try:
				result = await self._client.send.DOM.resolveNode(
					params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
				)
				if 'object' in result and 'objectId' in result['object']:
					object_id = result['object']['objectId']

					bounds_result = await self._client.send.Runtime.callFunctionOn(
						params={
							'functionDeclaration': """
								function() {
									const rect = this.getBoundingClientRect();
									return {
										x: rect.left, y: rect.top,
										width: rect.width, height: rect.height
									};
								}
							""",
							'objectId': object_id,
							'returnByValue': True,
						},
						session_id=self._session_id,
					)

					if 'result' in bounds_result and 'value' in bounds_result['result']:
						rect = bounds_result['result']['value']
						x, y, w, h = rect['x'], rect['y'], rect['width'], rect['height']
						quads = [[x, y, x + w, y, x + w, y + h, x, y + h]]
			except Exception:
				pass

		# If we still don't have quads, fall back to JS click
		if not quads:
			try:
				result = await self._client.send.DOM.resolveNode(
					params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
				)
				if 'object' in result and 'objectId' in result['object']:
					object_id = result['object']['objectId']
					await self._client.send.Runtime.callFunctionOn(
						params={
							'functionDeclaration': 'function() { this.click(); }',
							'objectId': object_id,
						},
						session_id=self._session_id,
					)
					return
			except Exception as e:
				raise RuntimeError(f'Failed to click element: {e}')

		# Calculate click coordinates from the best quad
		best_quad = quads[0]
		center_x = sum(best_quad[i] for i in range(0, 8, 2)) / 4
		center_y = sum(best_quad[i] for i in range(1, 8, 2)) / 4

		# Convert modifiers to CDP format
		modifier_value = 0
		if modifiers:
			modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
			for mod in modifiers:
				modifier_value |= modifier_map.get(mod, 0)

		# Scroll element into view
		try:
			await self._client.send.DOM.scrollIntoViewIfNeeded(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			await asyncio.sleep(0.05)  # Wait for scroll to complete
		except Exception:
			pass  # Continue even if scroll fails

		# Move mouse to element first
		try:
			await self._client.send.Input.dispatchMouseEvent(
				{
					'type': 'mouseMoved',
					'x': center_x,
					'y': center_y,
				},
				session_id=self._session_id,
			)
			await asyncio.sleep(0.05)
		except Exception:
			pass

		# Perform the click with timeout handling
		try:
			# Mouse press
			await asyncio.wait_for(
				self._client.send.Input.dispatchMouseEvent(
					{
						'type': 'mousePressed',
						'x': center_x,
						'y': center_y,
						'button': button,
						'clickCount': click_count,
						'modifiers': modifier_value,
					},
					session_id=self._session_id,
				),
				timeout=1.0,
			)
			await asyncio.sleep(0.08)
		except TimeoutError:
			pass  # Continue with mouse release

		# Mouse release
		try:
			await asyncio.wait_for(
				self._client.send.Input.dispatchMouseEvent(
					{
						'type': 'mouseReleased',
						'x': center_x,
						'y': center_y,
						'button': button,
						'clickCount': click_count,
						'modifiers': modifier_value,
					},
					session_id=self._session_id,
				),
				timeout=3.0,
			)
		except TimeoutError:
			pass  # Click may have succeeded even if release timed out

	async def fill(self, value: str) -> None:
		"""Fill the input element with text using robust clearing and typing."""
		# Focus the element first
		try:
			await self.focus()
		except Exception:
			logger.warning('Failed to focus element')

		# Get object ID for advanced operations
		result = await self._client.send.DOM.resolveNode(
			params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
		)
		if 'object' not in result or 'objectId' not in result['object']:
			raise RuntimeError('Cannot resolve element for text input')

		object_id = result['object']['objectId']

		# Strategy 1: Direct JavaScript value setting (most reliable)
		try:
			await self._client.send.Runtime.callFunctionOn(
				params={
					'functionDeclaration': """
						function() { 
							this.value = ""; 
							this.dispatchEvent(new Event("input", { bubbles: true })); 
							this.dispatchEvent(new Event("change", { bubbles: true })); 
							return this.value;
						}
					""",
					'objectId': object_id,
					'returnByValue': True,
				},
				session_id=self._session_id,
			)
		except Exception:
			# Strategy 2: Triple-click + Delete fallback
			try:
				# Get element bounds for triple-click
				bounds_result = await self._client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': 'function() { return this.getBoundingClientRect(); }',
						'objectId': object_id,
						'returnByValue': True,
					},
					session_id=self._session_id,
				)

				if bounds_result.get('result', {}).get('value'):
					bounds = bounds_result['result'].get('value')

					if not bounds:
						raise RuntimeError('Element bounds not found')

					center_x = bounds['x'] + bounds['width'] / 2
					center_y = bounds['y'] + bounds['height'] / 2

					# Triple-click to select all
					await self._client.send.Input.dispatchMouseEvent(
						{'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3},
						session_id=self._session_id,
					)
					await self._client.send.Input.dispatchMouseEvent(
						{'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3},
						session_id=self._session_id,
					)

					# Delete selected text
					await self._client.send.Input.dispatchKeyEvent(
						{'type': 'keyDown', 'key': 'Delete', 'code': 'Delete'},
						session_id=self._session_id,
					)
					await self._client.send.Input.dispatchKeyEvent(
						{'type': 'keyUp', 'key': 'Delete', 'code': 'Delete'},
						session_id=self._session_id,
					)
			except Exception:
				# Strategy 3: Ctrl/Cmd+A + Backspace
				import platform

				is_macos = platform.system() == 'Darwin'
				modifier = 4 if is_macos else 2  # Meta=4 (Cmd), Ctrl=2

				# Select all
				await self._client.send.Input.dispatchKeyEvent(
					{'type': 'keyDown', 'key': 'a', 'code': 'KeyA', 'modifiers': modifier},
					session_id=self._session_id,
				)
				await self._client.send.Input.dispatchKeyEvent(
					{'type': 'keyUp', 'key': 'a', 'code': 'KeyA', 'modifiers': modifier},
					session_id=self._session_id,
				)

				# Delete with Backspace
				await self._client.send.Input.dispatchKeyEvent(
					{'type': 'keyDown', 'key': 'Backspace', 'code': 'Backspace'},
					session_id=self._session_id,
				)
				await self._client.send.Input.dispatchKeyEvent(
					{'type': 'keyUp', 'key': 'Backspace', 'code': 'Backspace'},
					session_id=self._session_id,
				)

		# Type the new value character by character for better compatibility
		for char in value:
			# Send proper key events for each character
			await self._client.send.Input.dispatchKeyEvent(
				{'type': 'keyDown', 'key': char},
				session_id=self._session_id,
			)
			await self._client.send.Input.dispatchKeyEvent(
				{'type': 'char', 'text': char},
				session_id=self._session_id,
			)
			await self._client.send.Input.dispatchKeyEvent(
				{'type': 'keyUp', 'key': char},
				session_id=self._session_id,
			)
			await asyncio.sleep(0.001)  # Small delay for natural typing

	async def hover(self) -> None:
		"""Hover over the element."""
		box = await self.getBoundingBox()
		if not box:
			raise RuntimeError('Element is not visible or has no bounding box')

		x = box['x'] + box['width'] / 2
		y = box['y'] + box['height'] / 2

		params: 'DispatchMouseEventParameters' = {'type': 'mouseMoved', 'x': x, 'y': y}
		await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

	async def focus(self) -> None:
		"""Focus the element."""
		node_id = await self._get_node_id()
		params: 'FocusParameters' = {'nodeId': node_id}
		await self._client.send.DOM.focus(params, session_id=self._session_id)

	async def check(self) -> None:
		"""Check or uncheck a checkbox/radio button."""
		await self.click()

	async def selectOption(self, values: str | list[str]) -> None:
		"""Select option(s) in a select element."""
		if isinstance(values, str):
			values = [values]

		# Focus the element first
		try:
			await self.focus()
		except Exception:
			logger.warning('Failed to focus element')

		# For select elements, we need to find option elements and click them
		# This is a simplified approach - in practice, you might need to handle
		# different select types (single vs multi-select) differently
		node_id = await self._get_node_id()

		# Request child nodes to get the options
		params: 'RequestChildNodesParameters' = {'nodeId': node_id, 'depth': 1}
		await self._client.send.DOM.requestChildNodes(params, session_id=self._session_id)

		# Get the updated node description with children
		describe_params: 'DescribeNodeParameters' = {'nodeId': node_id, 'depth': 1}
		describe_result = await self._client.send.DOM.describeNode(describe_params, session_id=self._session_id)

		select_node = describe_result['node']

		# Find and select matching options
		for child in select_node.get('children', []):
			if child.get('nodeName', '').lower() == 'option':
				# Get option attributes
				attrs = child.get('attributes', [])
				option_attrs = {}
				for i in range(0, len(attrs), 2):
					if i + 1 < len(attrs):
						option_attrs[attrs[i]] = attrs[i + 1]

				option_value = option_attrs.get('value', '')
				option_text = child.get('nodeValue', '')

				# Check if this option should be selected
				should_select = option_value in values or option_text in values

				if should_select:
					# Click the option to select it
					option_node_id = child.get('nodeId')
					if option_node_id:
						# Get backend node ID for the option
						option_describe_params: 'DescribeNodeParameters' = {'nodeId': option_node_id}
						option_backend_result = await self._client.send.DOM.describeNode(
							option_describe_params, session_id=self._session_id
						)
						option_backend_id = option_backend_result['node']['backendNodeId']

						# Create an Element for the option and click it
						option_element = Element(self._browser_session, option_backend_id, self._session_id)
						await option_element.click()

	async def dragTo(
		self,
		target: Union['Element', Position],
		source_position: Position | None = None,
		target_position: Position | None = None,
	) -> None:
		"""Drag this element to another element or position."""
		# Get source coordinates
		if source_position:
			source_x = source_position['x']
			source_y = source_position['y']
		else:
			source_box = await self.getBoundingBox()
			if not source_box:
				raise RuntimeError('Source element is not visible')
			source_x = source_box['x'] + source_box['width'] / 2
			source_y = source_box['y'] + source_box['height'] / 2

		# Get target coordinates
		if isinstance(target, dict) and 'x' in target and 'y' in target:
			target_x = target['x']
			target_y = target['y']
		else:
			if target_position:
				target_box = await target.getBoundingBox()
				if not target_box:
					raise RuntimeError('Target element is not visible')
				target_x = target_box['x'] + target_position['x']
				target_y = target_box['y'] + target_position['y']
			else:
				target_box = await target.getBoundingBox()
				if not target_box:
					raise RuntimeError('Target element is not visible')
				target_x = target_box['x'] + target_box['width'] / 2
				target_y = target_box['y'] + target_box['height'] / 2

		# Perform drag operation
		await self._client.send.Input.dispatchMouseEvent(
			{'type': 'mousePressed', 'x': source_x, 'y': source_y, 'button': 'left'},
			session_id=self._session_id,
		)

		await self._client.send.Input.dispatchMouseEvent(
			{'type': 'mouseMoved', 'x': target_x, 'y': target_y},
			session_id=self._session_id,
		)

		await self._client.send.Input.dispatchMouseEvent(
			{'type': 'mouseReleased', 'x': target_x, 'y': target_y, 'button': 'left'},
			session_id=self._session_id,
		)

	# Element properties and queries
	async def getAttribute(self, name: str) -> str | None:
		"""Get an attribute value."""
		node_id = await self._get_node_id()
		params: 'GetAttributesParameters' = {'nodeId': node_id}
		result = await self._client.send.DOM.getAttributes(params, session_id=self._session_id)

		attributes = result['attributes']
		for i in range(0, len(attributes), 2):
			if attributes[i] == name:
				return attributes[i + 1]
		return None

	async def getBoundingBox(self) -> BoundingBox | None:
		"""Get the bounding box of the element."""
		try:
			node_id = await self._get_node_id()
			params: 'GetBoxModelParameters' = {'nodeId': node_id}
			result = await self._client.send.DOM.getBoxModel(params, session_id=self._session_id)

			if 'model' not in result:
				return None

			# Get content box (first 8 values are content quad: x1,y1,x2,y2,x3,y3,x4,y4)
			content = result['model']['content']
			if len(content) < 8:
				return None

			# Calculate bounding box from quad
			x_coords = [content[i] for i in range(0, 8, 2)]
			y_coords = [content[i] for i in range(1, 8, 2)]

			x = min(x_coords)
			y = min(y_coords)
			width = max(x_coords) - x
			height = max(y_coords) - y

			return BoundingBox(x=x, y=y, width=width, height=height)

		except Exception:
			return None

	async def screenshot(self, format: str = 'jpeg', quality: int | None = None) -> str:
		"""Take a screenshot of this element and return base64 encoded image.

		Args:
			format: Image format ('jpeg', 'png', 'webp')
			quality: Quality 0-100 for JPEG format

		Returns:
			Base64-encoded image data
		"""
		# Get element's bounding box
		box = await self.getBoundingBox()
		if not box:
			raise RuntimeError('Element is not visible or has no bounding box')

		# Create viewport clip for the element
		viewport: 'Viewport' = {'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height'], 'scale': 1.0}

		# Prepare screenshot parameters
		params: 'CaptureScreenshotParameters' = {'format': format, 'clip': viewport}

		if quality is not None and format.lower() == 'jpeg':
			params['quality'] = quality

		# Take screenshot
		result = await self._client.send.Page.captureScreenshot(params, session_id=self._session_id)

		return result['data']

	async def getBasicInfo(self) -> ElementInfo:
		"""Get basic information about the element including coordinates and properties."""
		try:
			# Get basic node information
			node_id = await self._get_node_id()
			describe_result = await self._client.send.DOM.describeNode({'nodeId': node_id}, session_id=self._session_id)

			node_info = describe_result['node']

			# Get bounding box
			bounding_box = await self.getBoundingBox()

			# Get attributes as a proper dict
			attributes_list = node_info.get('attributes', [])
			attributes_dict: dict[str, str] = {}
			for i in range(0, len(attributes_list), 2):
				if i + 1 < len(attributes_list):
					attributes_dict[attributes_list[i]] = attributes_list[i + 1]

			return ElementInfo(
				backendNodeId=self._backend_node_id,
				nodeId=node_id,
				nodeName=node_info.get('nodeName', ''),
				nodeType=node_info.get('nodeType', 0),
				nodeValue=node_info.get('nodeValue'),
				attributes=attributes_dict,
				boundingBox=bounding_box,
				error=None,
			)
		except Exception as e:
			return ElementInfo(
				backendNodeId=self._backend_node_id,
				nodeId=None,
				nodeName='',
				nodeType=0,
				nodeValue=None,
				attributes={},
				boundingBox=None,
				error=str(e),
			)
