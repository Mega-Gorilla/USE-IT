import logging
import time
from importlib import resources
from typing import Optional

from memory_profiler import memory_usage
from playwright.async_api import Page
from pympler import asizeof

from browser_use.dom.history_tree_processor.view import Coordinates
from browser_use.dom.views import (
	CoordinateSet,
	DOMBaseNode,
	DOMElementNode,
	DOMState,
	DOMTextNode,
	SelectorMap,
	ViewportInfo,
)

logger = logging.getLogger(__name__)


class DomService:
	def __init__(self, page: Page):
		self.page = page
		self.xpath_cache = {}

		self._iter = 0

	# region - Clickable elements
	async def get_clickable_elements(
		self,
		highlight_elements: bool = True,
		focus_element: int = -1,
		viewport_expansion: int = 0,
	) -> DOMState:
		element_tree = await self._build_dom_tree(highlight_elements, focus_element, viewport_expansion)
		selector_map = self._create_selector_map(element_tree)

		return DOMState(element_tree=element_tree, selector_map=selector_map)

	async def _build_dom_tree(
		self,
		highlight_elements: bool,
		focus_element: int,
		viewport_expansion: int,
	) -> DOMElementNode:
		js_code = resources.read_text('browser_use.dom', 'buildDomTree.js')

		args = {
			'doHighlightElements': highlight_elements,
			'focusHighlightIndex': focus_element,
			'viewportExpansion': viewport_expansion,
		}

		self._iter += 1
		logger.info(f'Iteration {self._iter} --------------------------------')

		mem_before = memory_usage(-1, interval=0.1, timeout=1)[0]

		logger.info(f'Eval Memory Usage: {mem_before} MB')
		eval_page = await self.page.evaluate(js_code, args)  # This is quite big, so be careful

		start_time = time.time()

		eval_page_deep_size = asizeof.asizeof(eval_page)
		logger.info(f'Deep size of eval_page: {eval_page_deep_size / 1024 / 1024:.2f} MB')

		mem_after = memory_usage(-1, interval=0.1, timeout=1)[0]

		logger.info(f'Eval Memory Usage: {mem_after} MB')

		html_to_dict = self._parse_node_iterative(eval_page)
		mem_after = memory_usage(-1, interval=0.1, timeout=1)[0]
		logger.info(f'Parse Memory Usage: {mem_after} MB')

		if html_to_dict is None or not isinstance(html_to_dict, DOMElementNode):
			raise ValueError('Failed to parse HTML to dictionary')

		html_to_dict_deep_size = asizeof.asizeof(html_to_dict)
		logger.info(f'Deep size of html_to_dict: {html_to_dict_deep_size / 1024 / 1024:.2f} MB')

		end_time = time.time()
		logger.info(f'Time taken: {end_time - start_time} seconds')

		return html_to_dict

	def _create_selector_map(self, element_tree: DOMElementNode) -> SelectorMap:
		selector_map = {}

		def process_node(node: DOMBaseNode):
			if isinstance(node, DOMElementNode):
				if node.highlight_index is not None:
					selector_map[node.highlight_index] = node

				for child in node.children:
					process_node(child)

		process_node(element_tree)
		return selector_map

	# @profile
	def _parse_node_iterative(
		self,
		root_data: dict,
		parent: Optional[DOMElementNode] = None,
	) -> Optional[DOMBaseNode]:
		if not root_data:
			return None

		# Process text nodes immediately
		if root_data.get('type') == 'TEXT_NODE':
			text_node = DOMTextNode(
				text=root_data['text'],
				is_visible=root_data['isVisible'],
				parent=parent,
			)
			return text_node

		# A helper to create the DOMElementNode like in your original code
		def create_element_node(node_data: dict, parent: Optional[DOMElementNode]) -> DOMElementNode:
			# Process coordinates if they exist for element nodes
			viewport_coordinates = None
			page_coordinates = None
			viewport_info = None

			if 'viewportCoordinates' in node_data:
				viewport_coordinates = CoordinateSet(
					top_left=Coordinates(**node_data['viewportCoordinates']['topLeft']),
					top_right=Coordinates(**node_data['viewportCoordinates']['topRight']),
					bottom_left=Coordinates(**node_data['viewportCoordinates']['bottomLeft']),
					bottom_right=Coordinates(**node_data['viewportCoordinates']['bottomRight']),
					center=Coordinates(**node_data['viewportCoordinates']['center']),
					width=node_data['viewportCoordinates']['width'],
					height=node_data['viewportCoordinates']['height'],
				)
			if 'pageCoordinates' in node_data:
				page_coordinates = CoordinateSet(
					top_left=Coordinates(**node_data['pageCoordinates']['topLeft']),
					top_right=Coordinates(**node_data['pageCoordinates']['topRight']),
					bottom_left=Coordinates(**node_data['pageCoordinates']['bottomLeft']),
					bottom_right=Coordinates(**node_data['pageCoordinates']['bottomRight']),
					center=Coordinates(**node_data['pageCoordinates']['center']),
					width=node_data['pageCoordinates']['width'],
					height=node_data['pageCoordinates']['height'],
				)
			if 'viewport' in node_data:
				viewport_info = ViewportInfo(
					scroll_x=node_data['viewport']['scrollX'],
					scroll_y=node_data['viewport']['scrollY'],
					width=node_data['viewport']['width'],
					height=node_data['viewport']['height'],
				)

			return DOMElementNode(
				tag_name=node_data['tagName'],
				xpath=node_data['xpath'],
				attributes=node_data.get('attributes', {}),
				children=[],  # initially empty
				is_visible=node_data.get('isVisible', False),
				is_interactive=node_data.get('isInteractive', False),
				is_top_element=node_data.get('isTopElement', False),
				highlight_index=node_data.get('highlightIndex'),
				shadow_root=node_data.get('shadowRoot', False),
				parent=parent,
				viewport_coordinates=viewport_coordinates,
				page_coordinates=page_coordinates,
				viewport_info=viewport_info,
			)

		# Create the root element node
		root_node = create_element_node(root_data, parent)

		# Stack will hold tuples of (node_data, DOMElementNode) where
		# node_data is the dict that might have children and DOMElementNode is
		# the parent node to attach those children to.
		stack: list[tuple[dict, DOMElementNode]] = [(root_data, root_node)]

		while stack:
			current_data, current_node = stack.pop()

			# Iterate over children in order (to maintain ordering, use normal order)
			# If you need the original order in the final children list, you can reverse the order in which you push
			for child_data in current_data.get('children', []):
				if child_data is None:
					continue

				if child_data.get('type') == 'TEXT_NODE':
					# Immediately create text nodes and attach them as children
					text_node = DOMTextNode(
						text=child_data['text'],
						is_visible=child_data['isVisible'],
						parent=current_node,
					)
					current_node.children.append(text_node)
				else:
					# Create element node and push it to the stack for further processing
					child_node = create_element_node(child_data, current_node)
					current_node.children.append(child_node)
					stack.append((child_data, child_node))

		return root_node
