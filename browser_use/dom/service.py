import asyncio
import logging
import time
from typing import TYPE_CHECKING

from cdp_use.cdp.accessibility.commands import GetFullAXTreeReturns
from cdp_use.cdp.accessibility.types import AXNode
from cdp_use.cdp.dom.types import Node
from cdp_use.cdp.target.types import TargetInfo

from browser_use.dom.enhanced_snapshot import (
	REQUIRED_COMPUTED_STYLES,
	build_snapshot_lookup,
)
from browser_use.dom.serializer.serializer import DOMTreeSerializer
from browser_use.dom.views import (
	CurrentPageTargets,
	DOMRect,
	EnhancedAXNode,
	EnhancedAXProperty,
	EnhancedDOMTreeNode,
	NodeType,
	SerializedDOMState,
	TargetAllTrees,
)

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession


# TODO: enable cross origin iframes -> experimental for now
ENABLE_CROSS_ORIGIN_IFRAMES = False


class DomService:
	"""
	Service for getting the DOM tree and other DOM-related information.

	Either browser or page must be provided.

	TODO: currently we start a new websocket connection PER STEP, we should definitely keep this persistent
	"""

	logger: logging.Logger

	def __init__(self, browser_session: 'BrowserSession', logger: logging.Logger | None = None):
		self.browser_session = browser_session
		self.logger = logger or logging.getLogger(__name__)

		# Cache for session domains that have been enabled
		self.session_id_domains_enabled_cache: dict[str, bool] = {}
		# Track sessions that need cleanup
		self._attached_sessions: set[str] = set()

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc_value, traceback):
		# Cleanup any attached sessions
		await self._cleanup_sessions()

	async def _get_targets_for_current_page(self) -> CurrentPageTargets:
		"""Get the target for the current page."""
		print('DomService._get_targets_for_current_page: Getting targets...')
		targets = await self.browser_session.cdp_client.send.Target.getTargets()
		print(f'DomService: Found {len(targets.get("targetInfos", []))} targets')

		# Use the current_target_id directly
		current_target_id = self.browser_session.current_target_id
		if not current_target_id:
			raise ValueError('No current target ID set in browser session')

		print(f'DomService: Looking for target with ID: {current_target_id}')

		# Find main page target by ID
		main_target = next((t for t in targets['targetInfos'] if t['targetId'] == current_target_id), None)

		if not main_target:
			# Debug: print all available targets
			print(f'DomService ERROR: No target found for current target ID: {current_target_id}')
			print('Available targets:')
			for t in targets['targetInfos']:
				print(f'  - {t["targetId"]}: type={t["type"]}, url={t.get("url", "none")}')
			raise ValueError(f'No target found for current target ID: {current_target_id}')

		print(f'DomService: Found main target: type={main_target["type"]}, url={main_target.get("url", "none")}')

		# Separate iframe targets for attachment
		iframe_targets = [t for t in targets['targetInfos'] if t['type'] == 'iframe']
		print(f'DomService: Found {len(iframe_targets)} iframe targets')

		return CurrentPageTargets(
			page_session=main_target,
			iframe_sessions=iframe_targets,
		)

	async def _attach_target_activate_domains_get_session_id(self, target: TargetInfo) -> str:
		"""This function is cached, so go crazy"""

		target_id = str(target['targetId'])
		print(f'DomService._attach_target: Attaching to target {target_id}, type={target.get("type")}')

		# if target_id in self.target_to_session_id_cache:
		# 	return self.target_to_session_id_cache[target_id]

		try:
			session = await self.browser_session.cdp_client.send.Target.attachToTarget(
				params={'targetId': target_id, 'flatten': True}
			)
			session_id = session['sessionId']
			print(f'DomService: Successfully attached, got session_id={session_id}')
		except Exception as e:
			print(f'DomService ERROR: Failed to attach to target {target_id}: {e}')
			raise

		# Track this session for cleanup
		self._attached_sessions.add(session_id)

		print(f'DomService: Enabling domains on session {session_id}...')
		await self._enable_all_domains_on_session(session_id)
		print(f'DomService: Domains enabled on session {session_id}')

		return session_id

	async def _cleanup_sessions(self) -> None:
		"""Detach from all attached sessions."""
		for session_id in self._attached_sessions:
			try:
				await self.browser_session.cdp_client.send.Target.detachFromTarget(params={'sessionId': session_id})
			except Exception:
				pass  # Session might already be detached
		self._attached_sessions.clear()
		self.session_id_domains_enabled_cache.clear()

	async def _enable_all_domains_on_session(self, session_id: str) -> None:
		if session_id in self.session_id_domains_enabled_cache:
			return

		await self.browser_session.cdp_client.send.Target.setAutoAttach(
			params={
				'autoAttach': True,
				'waitForDebuggerOnStart': False,
				'flatten': True,
			},
			session_id=session_id,
		)

		await asyncio.gather(
			self.browser_session.cdp_client.send.DOM.enable(session_id=session_id),
			self.browser_session.cdp_client.send.Accessibility.enable(session_id=session_id),
			self.browser_session.cdp_client.send.DOMSnapshot.enable(session_id=session_id),
			self.browser_session.cdp_client.send.Page.enable(session_id=session_id),
		)

		self.session_id_domains_enabled_cache[session_id] = True

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

	async def _get_viewport_ratio(self, session_id: str) -> float:
		"""Get viewport dimensions, device pixel ratio, and scroll position using CDP."""
		try:
			# Get the layout metrics which includes the visual viewport
			metrics = await self.browser_session.cdp_client.send.Page.getLayoutMetrics(session_id=session_id)

			visual_viewport = metrics.get('visualViewport', {})

			# IMPORTANT: Use CSS viewport instead of device pixel viewport
			# This fixes the coordinate mismatch on high-DPI displays
			css_visual_viewport = metrics.get('cssVisualViewport', {})
			css_layout_viewport = metrics.get('cssLayoutViewport', {})

			# Use CSS pixels (what JavaScript sees) instead of device pixels
			width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1920.0))

			# Calculate device pixel ratio
			device_width = visual_viewport.get('clientWidth', width)
			css_width = css_visual_viewport.get('clientWidth', width)
			device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0

			return float(device_pixel_ratio)
		except Exception as e:
			print(f'⚠️  Viewport size detection failed: {e}')
			# Fallback to default viewport size
			return 1.0

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

		"""
		Reverse iterate through the html frames (that can be either iframe or document -> if it's a document frame compare if the current bounds interest with it (taking scroll into account) otherwise move the current bounds by the iframe offset)
		"""
		for frame in reversed(html_frames):
			if (
				frame.node_type == NodeType.ELEMENT_NODE
				and frame.node_name.upper() == 'IFRAME'
				and frame.snapshot_node
				and frame.snapshot_node.bounds
			):
				iframe_bounds = frame.snapshot_node.bounds

				current_bounds.x += iframe_bounds.x
				current_bounds.y += iframe_bounds.y

			if (
				frame.node_type == NodeType.ELEMENT_NODE
				and frame.node_name == 'HTML'
				and frame.snapshot_node
				and frame.snapshot_node.scrollRects
				and frame.snapshot_node.clientRects
			):
				frame_left = frame.snapshot_node.scrollRects.x
				frame_top = frame.snapshot_node.scrollRects.y
				frame_right = frame.snapshot_node.scrollRects.x + frame.snapshot_node.clientRects.width
				frame_bottom = frame.snapshot_node.scrollRects.y + frame.snapshot_node.clientRects.height

				frame_intersects = (
					current_bounds.x < frame_right
					and current_bounds.x + current_bounds.width > frame_left
					and current_bounds.y < frame_bottom
					and current_bounds.y + current_bounds.height > frame_top
				)

				if not frame_intersects:
					return False

		# If we reach here, element is visible in main viewport and all containing iframes
		return True

	async def _get_ax_tree_for_all_frames(self, session_id: str) -> GetFullAXTreeReturns:
		"""Recursively collect all frames and merge their accessibility trees into a single array."""

		frame_tree = await self.browser_session.cdp_client.send.Page.getFrameTree(session_id=session_id)

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
			ax_tree_request = self.browser_session.cdp_client.send.Accessibility.getFullAXTree(
				params={'frameId': frame_id}, session_id=session_id
			)
			ax_tree_requests.append(ax_tree_request)

		# Wait for all requests to complete
		ax_trees = await asyncio.gather(*ax_tree_requests)

		# Merge all AX nodes into a single array
		merged_nodes: list[AXNode] = []
		for ax_tree in ax_trees:
			merged_nodes.extend(ax_tree['nodes'])

		return {'nodes': merged_nodes}

	async def _get_all_trees_for_session_id(self, session_id: str) -> TargetAllTrees:
		if not self.browser_session.cdp_url:
			raise ValueError('CDP URL is not set')

		print(f'DomService._get_all_trees_for_session_id: Starting with session_id={session_id}')

		# Wait for the page to be ready first
		try:
			print('DomService: Checking page ready state...')
			ready_state = await self.browser_session.cdp_client.send.Runtime.evaluate(
				params={'expression': 'document.readyState'}, session_id=session_id
			)
			print(f'DomService: Page ready state: {ready_state}')
		except Exception as e:
			print(f'Warning: Could not check page ready state: {e}')

		print('DomService: Creating CDP requests...')
		snapshot_request = self.browser_session.cdp_client.send.DOMSnapshot.captureSnapshot(
			params={
				'computedStyles': REQUIRED_COMPUTED_STYLES,
				'includePaintOrder': True,
				'includeDOMRects': True,
				'includeBlendedBackgroundColors': False,
				'includeTextColorOpacities': False,
			},
			session_id=session_id,
		)

		dom_tree_request = self.browser_session.cdp_client.send.DOM.getDocument(
			params={'depth': -1, 'pierce': True}, session_id=session_id
		)

		ax_tree_request = self._get_ax_tree_for_all_frames(session_id)

		device_pixel_ratio_request = self._get_viewport_ratio(session_id)

		start = time.time()
		# Use wait_for with timeout to debug which call is hanging
		try:
			print('DomService: Gathering all CDP requests...')
			snapshot, dom_tree, ax_tree, device_pixel_ratio = await asyncio.wait_for(
				asyncio.gather(snapshot_request, dom_tree_request, ax_tree_request, device_pixel_ratio_request), timeout=10.0
			)
			print('DomService: All CDP requests completed successfully')
		except TimeoutError:
			print('ERROR: CDP calls timed out in _get_all_trees_for_session_id')
			# Try to get them individually to see which one hangs
			print('Trying snapshot...')
			snapshot = await asyncio.wait_for(snapshot_request, timeout=2.0)
			print('Trying dom_tree...')
			dom_tree = await asyncio.wait_for(dom_tree_request, timeout=2.0)
			print('Trying ax_tree...')
			ax_tree = await asyncio.wait_for(ax_tree_request, timeout=2.0)
			print('Trying device_pixel_ratio...')
			device_pixel_ratio = await asyncio.wait_for(device_pixel_ratio_request, timeout=2.0)
		end = time.time()
		cdp_timing = {'cdp_calls_total': end - start}
		print(f'Time taken to get dom tree: {end - start} seconds')

		return TargetAllTrees(
			snapshot=snapshot,
			dom_tree=dom_tree,
			ax_tree=ax_tree,
			device_pixel_ratio=device_pixel_ratio,
			cdp_timing=cdp_timing,
		)

	async def get_dom_tree(
		self,
		target: TargetInfo,
		initial_html_frames: list[EnhancedDOMTreeNode] | None = None,
		initial_total_frame_offset: DOMRect | None = None,
	) -> EnhancedDOMTreeNode:
		# Get viewport dimensions first for visibility calculation

		session_id = await self._attach_target_activate_domains_get_session_id(target)

		trees = await self._get_all_trees_for_session_id(session_id)

		dom_tree = trees.dom_tree
		ax_tree = trees.ax_tree
		snapshot = trees.snapshot
		device_pixel_ratio = trees.device_pixel_ratio

		ax_tree_lookup: dict[int, AXNode] = {
			ax_node['backendDOMNodeId']: ax_node for ax_node in ax_tree['nodes'] if 'backendDOMNodeId' in ax_node
		}

		enhanced_dom_tree_node_lookup: dict[int, EnhancedDOMTreeNode] = {}
		""" NodeId (NOT backend node id) -> enhanced dom tree node"""  # way to get the parent/content node

		# Parse snapshot data with everything calculated upfront
		snapshot_lookup = build_snapshot_lookup(snapshot, device_pixel_ratio)

		async def _construct_enhanced_node(
			node: Node, html_frames: list[EnhancedDOMTreeNode] | None, total_frame_offset: DOMRect | None
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

			# to get rid of the pointer references
			if total_frame_offset is None:
				total_frame_offset = DOMRect(x=0.0, y=0.0, width=0.0, height=0.0)
			else:
				total_frame_offset = DOMRect(
					total_frame_offset.x, total_frame_offset.y, total_frame_offset.width, total_frame_offset.height
				)

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
					x=snapshot_data.bounds.x + total_frame_offset.x,
					y=snapshot_data.bounds.y + total_frame_offset.y,
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
				target_id=target['targetId'],
				content_document=None,
				shadow_root_type=shadow_root_type,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=enhanced_ax_node,
				snapshot_node=snapshot_data,
				is_visible=None,
				absolute_position=absolute_position,
				element_index=None,
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

				# and adjust the total frame offset by scroll
				if snapshot_data and snapshot_data.scrollRects:
					total_frame_offset.x -= snapshot_data.scrollRects.x
					total_frame_offset.y -= snapshot_data.scrollRects.y

			# Calculate new iframe offset for content documents, accounting for iframe scroll
			if node['nodeName'].upper() == 'IFRAME' and snapshot_data and snapshot_data.bounds:
				if snapshot_data.bounds:
					updated_html_frames.append(dom_tree_node)

					total_frame_offset.x += snapshot_data.bounds.x
					total_frame_offset.y += snapshot_data.bounds.y

			if 'contentDocument' in node and node['contentDocument']:
				dom_tree_node.content_document = await _construct_enhanced_node(
					node['contentDocument'], updated_html_frames, total_frame_offset
				)
				dom_tree_node.content_document.parent_node = dom_tree_node
				# forcefully set the parent node to the content document node (helps traverse the tree)

			if 'shadowRoots' in node and node['shadowRoots']:
				dom_tree_node.shadow_roots = []
				for shadow_root in node['shadowRoots']:
					shadow_root_node = await _construct_enhanced_node(shadow_root, updated_html_frames, total_frame_offset)
					# forcefully set the parent node to the shadow root node (helps traverse the tree)
					shadow_root_node.parent_node = dom_tree_node
					dom_tree_node.shadow_roots.append(shadow_root_node)

			if 'children' in node and node['children']:
				dom_tree_node.children_nodes = []
				for child in node['children']:
					dom_tree_node.children_nodes.append(
						await _construct_enhanced_node(child, updated_html_frames, total_frame_offset)
					)

			# Set visibility using the collected HTML frames
			dom_tree_node.is_visible = self.is_element_visible_according_to_all_parents(dom_tree_node, updated_html_frames)

			# handle cross origin iframe (just recursively call the main function with the proper target if it exists in iframes)
			# only do this if the iframe is visible (otherwise it's not worth it)

			if (
				# TODO: hacky way to disable cross origin iframes for now
				ENABLE_CROSS_ORIGIN_IFRAMES and node['nodeName'].upper() == 'IFRAME' and node.get('contentDocument', None) is None
			):  # None meaning there is no content
				targets = await self._get_targets_for_current_page()
				iframe_document_target = next(
					(iframe for iframe in targets.iframe_sessions if iframe.get('targetId') == node.get('frameId', None)), None
				)
				# if target actually exists in one of the frames, just recursively build the dom tree for it
				if iframe_document_target:
					print(f'🔍 Getting content document for iframe {node.get("frameId", None)}')
					content_document = await self.get_dom_tree(
						iframe_document_target,
						# TODO: experiment with this values -> not sure whether the whole cross origin iframe should be ALWAYS included as soon as some part of it is visible or not.
						# Current config: if the cross origin iframe is AT ALL visible, then just include everything inside of it!
						# initial_html_frames=updated_html_frames,
						initial_total_frame_offset=total_frame_offset,
					)

					dom_tree_node.content_document = content_document
					dom_tree_node.content_document.parent_node = dom_tree_node

			return dom_tree_node

		enhanced_dom_tree_node = await _construct_enhanced_node(dom_tree['root'], initial_html_frames, initial_total_frame_offset)

		return enhanced_dom_tree_node

	async def get_serialized_dom_tree(
		self, previous_cached_state: SerializedDOMState | None = None
	) -> tuple[SerializedDOMState, EnhancedDOMTreeNode, dict[str, float]]:
		"""Get the serialized DOM tree representation for LLM consumption.

		Returns:
			Tuple of (serialized_dom_state, enhanced_dom_tree_root, timing_info)
		"""

		try:
			print('DomService: Getting targets for current page...')
			page_session_info = await self._get_targets_for_current_page()
			print(f'DomService: Got page session info, target_id={page_session_info.page_session.get("targetId")}')

			print('DomService: Getting DOM tree...')
			enhanced_dom_tree = await self.get_dom_tree(page_session_info.page_session)
			print('DomService: Got enhanced DOM tree')

			start = time.time()
			serialized_dom_state, serializer_timing = DOMTreeSerializer(
				enhanced_dom_tree, previous_cached_state
			).serialize_accessible_elements()

			end = time.time()
			serialize_total_timing = {'serialize_dom_tree_total': end - start}
			print(f'Time taken to serialize dom tree: {end - start} seconds')

			# Combine all timing info
			all_timing = {**serializer_timing, **serialize_total_timing}

			return serialized_dom_state, enhanced_dom_tree, all_timing

		finally:
			# Cleanup any attached sessions
			await self._cleanup_sessions()
