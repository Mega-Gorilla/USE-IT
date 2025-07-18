import asyncio
import time
from typing import TYPE_CHECKING

import httpx
from cdp_use import CDPClient
from cdp_use.cdp.accessibility.commands import GetFullAXTreeReturns
from cdp_use.cdp.accessibility.types import AXNode
from cdp_use.cdp.dom.commands import GetDocumentReturns
from cdp_use.cdp.dom.types import Node
from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns

from browser_use.dom.enhanced_snapshot import (
	REQUIRED_COMPUTED_STYLES,
	build_snapshot_lookup,
)
from browser_use.dom.serializer.serializer import DOMTreeSerializer
from browser_use.dom.views import (
	DOMRect,
	EnhancedAXNode,
	EnhancedAXProperty,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
)

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession
	from browser_use.browser.types import Page


class DomService:
	"""
	Service for getting the DOM tree and other DOM-related information.

	Either browser or page must be provided.

	TODO: currently we start a new websocket connection PER STEP, we should definitely keep this persistent
	"""

	def __init__(self, browser: 'BrowserSession', page: 'Page'):
		self.browser = browser
		self.page = page

		self.cdp_client: CDPClient | None = None
		# self.playwright_page_to_session_id_store: dict[str, str] = {}

	async def _get_cdp_client(self) -> CDPClient:
		if not self.browser.cdp_url:
			raise ValueError('CDP URL is not set')

		# TODO: MOVE THIS TO BROWSER SESSION (or sth idk)
		# If the cdp_url is already a websocket URL, use it as-is.
		if self.browser.cdp_url.startswith('ws'):
			ws_url = self.browser.cdp_url
		else:
			# Otherwise, treat it as the DevTools HTTP root and fetch the websocket URL.
			url = self.browser.cdp_url.rstrip('/')
			if not url.endswith('/json/version'):
				url = url + '/json/version'
			async with httpx.AsyncClient() as client:
				version_info = await client.get(url)
				ws_url = version_info.json()['webSocketDebuggerUrl']

		if self.cdp_client is None:
			self.cdp_client = CDPClient(ws_url)
			await self.cdp_client.start()

		return self.cdp_client

	async def __aenter__(self):
		await self._get_cdp_client()
		return self

	# on self destroy -> stop the cdp client
	async def __aexit__(self, exc_type, exc_value, traceback):
		if self.cdp_client:
			await self.cdp_client.stop()
			self.cdp_client = None

	async def _get_current_page_session_id(self) -> str:
		"""Get the target ID for a playwright page.

		TODO: this is a REALLY hacky way -> if multiple same urls are open then this will break
		"""
		# page_guid = self.page._impl_obj._guid
		# TODO: add cache for page to sessionId

		# if page_guid in self.page_to_session_id_store:
		# 	return self.page_to_session_id_store[page_guid]

		cdp_client = await self._get_cdp_client()

		targets = await cdp_client.send.Target.getTargets()
		for target in targets['targetInfos']:
			if target['type'] == 'page' and target['url'] == self.page.url:
				# cache the session id for this playwright page
				# self.playwright_page_to_session_id_store[page_guid] = target['targetId']

				session = await cdp_client.send.Target.attachToTarget(params={'targetId': target['targetId'], 'flatten': True})
				session_id = session['sessionId']

				await cdp_client.send.Target.setAutoAttach(
					params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
				)

				await cdp_client.send.DOM.enable(session_id=session_id)
				await cdp_client.send.Accessibility.enable(session_id=session_id)
				await cdp_client.send.DOMSnapshot.enable(session_id=session_id)
				await cdp_client.send.Page.enable(session_id=session_id)

				return session_id

		raise ValueError(f'No session id found for page {self.page.url}')

	def _build_enhanced_ax_node(self, ax_node: AXNode) -> EnhancedAXNode:
		properties: list[EnhancedAXProperty] | None = None
		if 'properties' in ax_node and ax_node['properties']:
			properties = []
			for property in ax_node['properties']:
				try:
					# test whether property name can go into the enum (sometimes Chrome returns some random properties)
					properties.append(
						EnhancedAXProperty(
							name=property['name'],
							value=property.get('value', {}).get('value', None),
							# related_nodes=[],  # TODO: add related nodes
						)
					)
				except ValueError:
					pass

		enhanced_ax_node = EnhancedAXNode(
			ax_node_id=ax_node['nodeId'],
			ignored=ax_node['ignored'],
			role=ax_node.get('role', {}).get('value', None),
			name=ax_node.get('name', {}).get('value', None),
			description=ax_node.get('description', {}).get('value', None),
			properties=properties,
		)
		return enhanced_ax_node

	async def _get_viewport_size(self) -> tuple[float, float, float, float, float]:
		"""Get viewport dimensions, device pixel ratio, and scroll position using CDP."""
		try:
			cdp_client = await self._get_cdp_client()
			session_id = await self._get_current_page_session_id()

			# Get the layout metrics which includes the visual viewport
			metrics = await cdp_client.send.Page.getLayoutMetrics(session_id=session_id)

			visual_viewport = metrics.get('visualViewport', {})
			layout_viewport = metrics.get('layoutViewport', {})
			content_size = metrics.get('contentSize', {})

			# IMPORTANT: Use CSS viewport instead of device pixel viewport
			# This fixes the coordinate mismatch on high-DPI displays
			css_visual_viewport = metrics.get('cssVisualViewport', {})
			css_layout_viewport = metrics.get('cssLayoutViewport', {})

			# Use CSS pixels (what JavaScript sees) instead of device pixels
			width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1920.0))
			height = css_visual_viewport.get('clientHeight', css_layout_viewport.get('clientHeight', 1080.0))

			# Calculate device pixel ratio
			device_width = visual_viewport.get('clientWidth', width)
			css_width = css_visual_viewport.get('clientWidth', width)
			device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0

			# Get current scroll position from the visual viewport
			scroll_x = css_visual_viewport.get('pageX', 0)
			scroll_y = css_visual_viewport.get('pageY', 0)

			return float(width), float(height), float(device_pixel_ratio), float(scroll_x), float(scroll_y)
		except Exception as e:
			print(f'⚠️  Viewport size detection failed: {e}')
			# Fallback to default viewport size
			return 1920.0, 1080.0, 1.0, 0.0, 0.0

	@classmethod
	def is_element_visible_according_to_all_parents(
		cls, node: EnhancedDOMTreeNode, html_frames: list[EnhancedDOMTreeNode]
	) -> bool:
		"""Check if the element is visible according to all its parent HTML frames."""

		if not node.snapshot_node:
			return False

		computed_styles = node.snapshot_node.computed_styles or {}

		display = computed_styles.get('display', '').lower()
		visibility = computed_styles.get('visibility', '').lower()
		opacity = computed_styles.get('opacity', '1')

		if display == 'none' or visibility == 'hidden':
			return False

		try:
			if float(opacity) <= 0:
				return False
		except (ValueError, TypeError):
			pass

		# Start with the element's local bounds (in its own frame's coordinate system)
		current_bounds = node.snapshot_node.bounds

		if not current_bounds:
			return False  # If there are no bounds, the element is not visible

		# Find the main document frame (the one not inside an iframe)
		main_frame = None
		for frame in html_frames:
			# Check if this frame has a parent iframe
			current_node = frame.parent_node
			is_in_iframe = False
			while current_node:
				if current_node.node_type == NodeType.ELEMENT_NODE and current_node.node_name.upper() == 'IFRAME':
					is_in_iframe = True
					break
				current_node = current_node.parent_node

			# If this frame is not inside an iframe, it's likely the main document
			if not is_in_iframe:
				main_frame = frame
				break

		# Check visibility against main document viewport and all relevant iframe viewports
		if not node.absolute_position:
			return False  # Need absolute position for accurate visibility checking

		elem_bounds = node.absolute_position
		elem_left = elem_bounds.x
		elem_top = elem_bounds.y
		elem_right = elem_bounds.x + elem_bounds.width
		elem_bottom = elem_bounds.y + elem_bounds.height

		# First: Check against main document viewport
		if (
			main_frame
			and main_frame.snapshot_node
			and main_frame.snapshot_node.scrollRects
			and main_frame.snapshot_node.clientRects
		):
			scroll_rects = main_frame.snapshot_node.scrollRects
			client_rects = main_frame.snapshot_node.clientRects

			# Main document viewport: currently visible area
			main_viewport_left = scroll_rects.x
			main_viewport_top = scroll_rects.y
			main_viewport_right = scroll_rects.x + client_rects.width
			main_viewport_bottom = scroll_rects.y + client_rects.height

			# Check if element is within main viewport
			main_viewport_intersects = (
				elem_right > main_viewport_left
				and elem_left < main_viewport_right
				and elem_bottom > main_viewport_top
				and elem_top < main_viewport_bottom
			)

			if not main_viewport_intersects:
				return False

		# Second: Check against each iframe's viewport that contains this element
		# Work through the frame hierarchy to ensure element is visible in all containing iframes
		current_node = node.parent_node
		while current_node:
			# If we find an iframe ancestor, check if element is visible within that iframe
			if (
				current_node.node_type == NodeType.ELEMENT_NODE
				and current_node.node_name.upper() == 'IFRAME'
				and current_node.snapshot_node
				and current_node.snapshot_node.bounds
			):
				iframe_bounds = current_node.snapshot_node.bounds
				iframe_left = iframe_bounds.x
				iframe_top = iframe_bounds.y
				iframe_right = iframe_bounds.x + iframe_bounds.width
				iframe_bottom = iframe_bounds.y + iframe_bounds.height

				# Check if element (in absolute coordinates) intersects with iframe bounds
				iframe_intersects = (
					elem_right > iframe_left
					and elem_left < iframe_right
					and elem_bottom > iframe_top
					and elem_top < iframe_bottom
				)

				if not iframe_intersects:
					return False

			current_node = current_node.parent_node

		# If we reach here, element is visible in main viewport and all containing iframes
		return True

	async def _build_enhanced_dom_tree(
		self, dom_tree: GetDocumentReturns, ax_tree: GetFullAXTreeReturns, snapshot: CaptureSnapshotReturns
	) -> EnhancedDOMTreeNode:
		ax_tree_lookup: dict[int, AXNode] = {
			ax_node['backendDOMNodeId']: ax_node for ax_node in ax_tree['nodes'] if 'backendDOMNodeId' in ax_node
		}

		enhanced_dom_tree_node_lookup: dict[int, EnhancedDOMTreeNode] = {}
		""" NodeId (NOT backend node id) -> enhanced dom tree node"""  # way to get the parent/content node

		# Get viewport dimensions first for visibility calculation
		viewport_width, viewport_height, device_pixel_ratio, scroll_x, scroll_y = await self._get_viewport_size()

		# Parse snapshot data with everything calculated upfront
		snapshot_lookup = build_snapshot_lookup(snapshot, device_pixel_ratio)

		def _construct_enhanced_node(
			node: Node, html_frames: list[EnhancedDOMTreeNode] | None = None, accumulated_iframe_offset: DOMRect | None = None
		) -> EnhancedDOMTreeNode:
			"""
			Recursively construct enhanced DOM tree nodes.

			Args:
				node: The DOM node to construct
				html_frames: List of HTML frame nodes encountered so far
				accumulated_iframe_offset: Accumulated coordinate translation from parent iframes (includes scroll corrections)
			"""
			# Initialize lists if not provided
			if html_frames is None:
				html_frames = []
			if accumulated_iframe_offset is None:
				accumulated_iframe_offset = DOMRect(x=0.0, y=0.0, width=0.0, height=0.0)

			# memoize the mf (I don't know if some nodes are duplicated)
			if node['nodeId'] in enhanced_dom_tree_node_lookup:
				return enhanced_dom_tree_node_lookup[node['nodeId']]

			ax_node = ax_tree_lookup.get(node['backendNodeId'])
			if ax_node:
				enhanced_ax_node = self._build_enhanced_ax_node(ax_node)
			else:
				enhanced_ax_node = None

			# To make attributes more readable
			attributes: dict[str, str] | None = None
			if 'attributes' in node and node['attributes']:
				attributes = {}
				for i in range(0, len(node['attributes']), 2):
					attributes[node['attributes'][i]] = node['attributes'][i + 1]

			shadow_root_type = None
			if 'shadowRootType' in node and node['shadowRootType']:
				try:
					shadow_root_type = node['shadowRootType']
				except ValueError:
					pass

			# Get snapshot data and calculate absolute position
			snapshot_data = snapshot_lookup.get(node['backendNodeId'], None)
			absolute_position = None
			if snapshot_data and snapshot_data.bounds:
				absolute_position = DOMRect(
					x=snapshot_data.bounds.x + accumulated_iframe_offset.x,
					y=snapshot_data.bounds.y + accumulated_iframe_offset.y,
					width=snapshot_data.bounds.width,
					height=snapshot_data.bounds.height,
				)

			dom_tree_node = EnhancedDOMTreeNode(
				node_id=node['nodeId'],
				backend_node_id=node['backendNodeId'],
				node_type=NodeType(node['nodeType']),
				node_name=node['nodeName'],
				node_value=node['nodeValue'],
				attributes=attributes or {},
				is_scrollable=node.get('isScrollable', None),
				frame_id=node.get('frameId', None),
				content_document=None,
				shadow_root_type=shadow_root_type,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=enhanced_ax_node,
				snapshot_node=snapshot_data,
				is_visible=None,
				absolute_position=absolute_position,
			)

			enhanced_dom_tree_node_lookup[node['nodeId']] = dom_tree_node

			if 'parentId' in node and node['parentId']:
				dom_tree_node.parent_node = enhanced_dom_tree_node_lookup[
					node['parentId']
				]  # parents should always be in the lookup

			# Check if this is an HTML frame node and add it to the list
			updated_html_frames = html_frames.copy()
			if node['nodeType'] == NodeType.ELEMENT_NODE.value and node['nodeName'] == 'HTML' and node.get('frameId') is not None:
				updated_html_frames.append(dom_tree_node)

			# Calculate new iframe offset for content documents, accounting for iframe scroll
			new_iframe_offset = accumulated_iframe_offset
			if node['nodeName'].upper() == 'IFRAME' and snapshot_data and snapshot_data.bounds:
				# Start with iframe position in parent frame
				iframe_x_offset = accumulated_iframe_offset.x + snapshot_data.bounds.x
				iframe_y_offset = accumulated_iframe_offset.y + snapshot_data.bounds.y

				# Account for iframe content scroll if we have content document
				if 'contentDocument' in node and node['contentDocument']:
					# Look for the HTML node in the content document to get its scroll position
					content_doc = node['contentDocument']
					if 'children' in content_doc:
						for child in content_doc['children']:
							if child.get('nodeType') == NodeType.ELEMENT_NODE.value and child.get('nodeName') == 'HTML':
								# Get HTML frame's snapshot data for scroll info
								html_snapshot = snapshot_lookup.get(child['backendNodeId'], None)
								if html_snapshot and html_snapshot.scrollRects:
									# Subtract scroll position (scrollRects.x/y represents current scroll offset)
									iframe_x_offset -= html_snapshot.scrollRects.x
									iframe_y_offset -= html_snapshot.scrollRects.y
								break

				new_iframe_offset = DOMRect(
					x=iframe_x_offset,
					y=iframe_y_offset,
					width=0.0,  # width/height not needed for offset calculation
					height=0.0,
				)

			if 'contentDocument' in node and node['contentDocument']:
				dom_tree_node.content_document = _construct_enhanced_node(
					node['contentDocument'], updated_html_frames, new_iframe_offset
				)
				dom_tree_node.content_document.parent_node = dom_tree_node
				# forcefully set the parent node to the content document node (helps traverse the tree)

			if 'shadowRoots' in node and node['shadowRoots']:
				dom_tree_node.shadow_roots = []
				for shadow_root in node['shadowRoots']:
					shadow_root_node = _construct_enhanced_node(shadow_root, updated_html_frames, accumulated_iframe_offset)
					# forcefully set the parent node to the shadow root node (helps traverse the tree)
					shadow_root_node.parent_node = dom_tree_node
					dom_tree_node.shadow_roots.append(shadow_root_node)

			if 'children' in node and node['children']:
				dom_tree_node.children_nodes = []
				for child in node['children']:
					dom_tree_node.children_nodes.append(
						_construct_enhanced_node(child, updated_html_frames, accumulated_iframe_offset)
					)

			# Set visibility using the collected HTML frames
			dom_tree_node.is_visible = self.is_element_visible_according_to_all_parents(dom_tree_node, updated_html_frames)

			return dom_tree_node

		enhanced_dom_tree_node = _construct_enhanced_node(dom_tree['root'])

		return enhanced_dom_tree_node

	async def _get_ax_tree_for_all_frames(self, cdp_client: CDPClient, session_id: str) -> GetFullAXTreeReturns:
		"""Recursively collect all frames and merge their accessibility trees into a single array."""
		frame_tree = await cdp_client.send.Page.getFrameTree(session_id=session_id)

		def collect_all_frame_ids(frame_tree_node) -> list[str]:
			"""Recursively collect all frame IDs from the frame tree."""
			frame_ids = [frame_tree_node['frame']['id']]

			if 'childFrames' in frame_tree_node and frame_tree_node['childFrames']:
				for child_frame in frame_tree_node['childFrames']:
					frame_ids.extend(collect_all_frame_ids(child_frame))

			return frame_ids

		# Collect all frame IDs recursively
		all_frame_ids = collect_all_frame_ids(frame_tree['frameTree'])

		# Get accessibility tree for each frame
		ax_tree_requests = []
		for frame_id in all_frame_ids:
			ax_tree_request = cdp_client.send.Accessibility.getFullAXTree(params={'frameId': frame_id}, session_id=session_id)
			ax_tree_requests.append(ax_tree_request)

		# Wait for all requests to complete
		ax_trees = await asyncio.gather(*ax_tree_requests)

		# Merge all AX nodes into a single array
		merged_nodes: list[AXNode] = []
		for ax_tree in ax_trees:
			merged_nodes.extend(ax_tree['nodes'])

		return {'nodes': merged_nodes}

	async def _get_all_trees(self) -> tuple[CaptureSnapshotReturns, GetDocumentReturns, GetFullAXTreeReturns, dict[str, float]]:
		if not self.browser.cdp_url:
			raise ValueError('CDP URL is not set')

		session_id = await self._get_current_page_session_id()
		cdp_client = await self._get_cdp_client()

		snapshot_request = cdp_client.send.DOMSnapshot.captureSnapshot(
			params={
				'computedStyles': REQUIRED_COMPUTED_STYLES,
				'includePaintOrder': True,
				'includeDOMRects': True,
				'includeBlendedBackgroundColors': False,
				'includeTextColorOpacities': False,
			},
			session_id=session_id,
		)

		dom_tree_request = cdp_client.send.DOM.getDocument(params={'depth': -1, 'pierce': True}, session_id=session_id)

		ax_tree_request = self._get_ax_tree_for_all_frames(cdp_client, session_id)

		start = time.time()
		snapshot, dom_tree, ax_tree = await asyncio.gather(snapshot_request, dom_tree_request, ax_tree_request)
		end = time.time()
		cdp_timing = {'cdp_calls_total': end - start}
		print(f'Time taken to get dom tree: {end - start} seconds')

		return snapshot, dom_tree, ax_tree, cdp_timing

	async def get_dom_tree(self) -> tuple[EnhancedDOMTreeNode, dict[str, float]]:
		snapshot, dom_tree, ax_tree, cdp_timing = await self._get_all_trees()

		start = time.time()
		enhanced_dom_tree = await self._build_enhanced_dom_tree(dom_tree, ax_tree, snapshot)
		end = time.time()

		build_tree_timing = {'build_enhanced_dom_tree': end - start}
		print(f'Time taken to build enhanced dom tree: {end - start} seconds')

		# Combine timing info
		all_timing = {**cdp_timing, **build_tree_timing}
		return enhanced_dom_tree, all_timing

	async def get_serialized_dom_tree(
		self, previous_cached_state: SerializedDOMState | None = None
	) -> tuple[SerializedDOMState, dict[str, float]]:
		"""Get the serialized DOM tree representation for LLM consumption.

		TODO: this is a bit of a hack, we should probably have a better way to do this
		"""
		enhanced_dom_tree, dom_timing = await self.get_dom_tree()

		start = time.time()
		serialized_dom_state, serializer_timing = DOMTreeSerializer(
			enhanced_dom_tree, previous_cached_state
		).serialize_accessible_elements()

		end = time.time()
		serialize_total_timing = {'serialize_dom_tree_total': end - start}
		print(f'Time taken to serialize dom tree: {end - start} seconds')

		# Combine all timing info
		all_timing = {**dom_timing, **serializer_timing, **serialize_total_timing}
		return serialized_dom_state, all_timing
