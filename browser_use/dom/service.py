import gc
import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Optional, TypeVar

if TYPE_CHECKING:
	from playwright.async_api import Page

from browser_use.dom.views import (
	DOMBaseNode,
	DOMElementNode,
	DOMState,
	DOMTextNode,
	SelectorMap,
)
from browser_use.utils import time_execution_async

logger = logging.getLogger(__name__)


@dataclass
class ViewportInfo:
	width: int
	height: int


T = TypeVar('T', dict, list, int)


def merge_dom_trees(value_a: T, value_b: T) -> T:
	"""merge two buildDomTree.js result dicts. used to put together results from parent frame and child cross-origin iframes"""

	if isinstance(value_a, dict) and isinstance(value_b, dict):
		merged_tree = value_a.copy()
		for key, value in value_b.items():
			if key == 'rootId':
				# original top parent rootId should never change
				continue

			if key in merged_tree:
				merged_tree[key] = merge_dom_trees(merged_tree[key], value)
			else:
				merged_tree[key] = value

		return merged_tree

	elif isinstance(value_a, list) and isinstance(value_b, list):
		merged_tree = value_a.copy()
		for value in value_b:
			if value not in merged_tree:
				merged_tree.append(value)

		return merged_tree

	elif isinstance(value_a, (int, float)) and isinstance(value_b, (int, float)):
		# assumes all numeric values in perf metrics are summable totals counts or seconds/milliseconds
		# may not always be true in the future, e.g. if we add any avg values then naiive sum will be wrong
		return value_a + value_b

	elif isinstance(value_a, str) and isinstance(value_b, str):
		if value_a == value_b:
			return value_a

	raise TypeError(f'Cannot merge {type(value_a)}: {value_a} and {type(value_b)}: {value_b}')


def link_cross_origin_iframes(eval_page: dict) -> dict:
	"""link cross-origin iframes to their parent frame"""
	for node in eval_page['map'].values():
		if node['tagName'] == 'iframe' and not node['children']:
			# locate the child frame's body and put it in the children
			# so that it's handled the same as a same-origin iframe
			for elem in eval_page['map'].values():
				if elem.get('parentFrameId') == node['id']:
					node['children'].append(elem)
					break

	return eval_page


def dump_frame_tree(frame, indent=''):
	"""From: https://playwright.dev/python/docs/api/class-frame"""
	yield (indent + frame.name + '@' + frame.url)
	for child in frame.child_frames:
		yield from dump_frame_tree(child, indent + '    ')


# def find_nested_iframes(root_frame) -> dict[str, 'Frame']:
# 	"""
# 	iteratively find all iframes in the page
# 	assumptions:
# 	- iframes can be nested
# 	- iframes can be cross-origin-iframes
# 	- non-cross-origin iframes can nested inside cross-origin iframes
# 	- iframes arent guaranteed to have any id, name, or unique src url
# 	- iframes and their content can be added/removed/updated multiple times per second by JS
# 	"""

# 	frame_idx = 0

# 	all_frames = {f'{frame_idx}': root_frame}
# 	frames_to_search = [list(all_frames.keys())[0]]
# 	while frames_to_search:
# 		frame_idx += 1
# 		frame_id = frames_to_search.pop()
# 		frame = all_frames[frame_id]

# 		for child_frame in frame.child_frames if hasattr(frame, 'child_frames') else frame.frames:
# 			child_frame_id = f'{frame_id}/{frame_idx}'
# 			all_frames[child_frame_id] = child_frame
# 			frames_to_search.append(child_frame_id)

# 	return all_frames


class DomService:
	def __init__(self, page: 'Page'):
		self.page = page
		self.xpath_cache = {}

		self.js_code = resources.read_text('browser_use.dom', 'buildDomTree.js')

	# region - Clickable elements
	@time_execution_async('--get_clickable_elements')
	async def get_clickable_elements(
		self,
		highlight_elements: bool = True,
		focus_element: int = -1,
		viewport_expansion: int = 0,
	) -> DOMState:
		element_tree, selector_map = await self._build_dom_tree(highlight_elements, focus_element, viewport_expansion)
		return DOMState(element_tree=element_tree, selector_map=selector_map)

	@time_execution_async('--build_dom_tree')
	async def _build_dom_tree(
		self,
		highlight_elements: bool,
		focus_element: int,
		viewport_expansion: int,
	) -> tuple[DOMElementNode, SelectorMap]:
		if await self.page.evaluate('1+1') != 2:
			raise ValueError('The page cannot evaluate javascript code properly')

		if self.page.url == 'about:blank':
			# short-circuit if the page is a new empty tab for speed, no need to inject buildDomTree.js
			return (
				DOMElementNode(
					tag_name='body',
					xpath='',
					attributes={},
					children=[],
					is_visible=False,
					parent=None,
				),
				{},
			)

		# NOTE: We execute JS code in the browser to extract important DOM information.
		#       The returned hash map contains information about the DOM tree and the
		#       relationship between the DOM elements.
		debug_mode = logger.getEffectiveLevel() == logging.DEBUG
		args = {
			'doHighlightElements': highlight_elements,
			'focusHighlightIndex': focus_element,
			'viewportExpansion': viewport_expansion,
			'debugMode': debug_mode,
		}

		logger.debug('Running buildDomTree.js on: %s', self.page.url)

		# 1. recursively find all nested iframes
		# 2. filter to only include cross-origin iframes
		# 3. run buildDomTree.js on all cross-origin iframes + top-level-page in parallel
		# 4. merge the results into a single flat selector_map

		print(list(dump_frame_tree(self.page.main_frame)))

		try:
			eval_page: dict = await self.page.evaluate(self.js_code, args)
		except Exception as e:
			logger.error('Error evaluating JavaScript: %s', e)
			raise

		# # also run buildDomTree.js on all cross-origin iframes
		# inner_iframes = [
		# 	await frame.frame_element()
		# 	for frame in self.page.frames
		# 	if frame.url != self.page.url and not frame.url.startswith('data:')
		# ]
		# for frame in inner_iframes:
		# 	try:
		# 		# check if the frame already has a buildDomTree.js
		# 		inner_target = await frame.content_frame()
		# 		has_built_dom_tree = await inner_target.evaluate('window._loadedBrowserUseBuildDomTree')
		# 		if has_built_dom_tree:
		# 			# was already processed by buildDomTree.js's iframe-traveral handling
		# 			continue
		# 		else:
		# 			logger.debug('Found cross-origin iframe that needs special handling: %s', inner_target.url)
		# 			# start the index offset from the last index of the parent frame so that child element idx dont conflict
		# 			iframe_args = {
		# 				**args,
		# 				'indexOffset': len(eval_page['map']),
		# 				'parentFrameId': frame.parent_frame_id,
		# 			}
		# 			iframe_results: dict = await inner_target.evaluate(self.js_code, iframe_args)
		# 			eval_page: dict = merge_dom_trees(eval_page, iframe_results)

		# 	except Exception as e:
		# 		logger.error('Error checking page for cross origin iframes: %s', e)
		# 		# frames often dissapear upon navigation, page changes, etc. no need to hard fail here
		# 		continue

		# eval_page = link_cross_origin_iframes(eval_page)

		# Only log performance metrics in debug mode
		if debug_mode and 'perfMetrics' in eval_page:
			logger.debug('DOM Tree Building Performance Metrics:\n%s', json.dumps(eval_page['perfMetrics'], indent=2))

		return await self._construct_dom_tree(eval_page)

	@time_execution_async('--construct_dom_tree')
	async def _construct_dom_tree(
		self,
		eval_page: dict,
	) -> tuple[DOMElementNode, SelectorMap]:
		js_node_map = eval_page['map']
		js_root_id = eval_page['rootId']

		selector_map = {}
		node_map = {}

		for id, node_data in js_node_map.items():
			node, children_ids = self._parse_node(node_data)
			if node is None:
				continue

			node_map[id] = node

			if isinstance(node, DOMElementNode) and node.highlight_index is not None:
				selector_map[node.highlight_index] = node

			# NOTE: We know that we are building the tree bottom up
			#       and all children are already processed.
			if isinstance(node, DOMElementNode):
				for child_id in children_ids:
					if child_id not in node_map:
						continue

					child_node = node_map[child_id]

					child_node.parent = node
					node.children.append(child_node)

		html_to_dict = node_map[str(js_root_id)]

		del node_map
		del js_node_map
		del js_root_id

		gc.collect()

		if html_to_dict is None or not isinstance(html_to_dict, DOMElementNode):
			raise ValueError('Failed to parse HTML to dictionary')

		return html_to_dict, selector_map

	def _parse_node(
		self,
		node_data: dict,
	) -> tuple[Optional[DOMBaseNode], list[int]]:
		if not node_data:
			return None, []

		# Process text nodes immediately
		if node_data.get('type') == 'TEXT_NODE':
			text_node = DOMTextNode(
				text=node_data['text'],
				is_visible=node_data['isVisible'],
				parent=None,
			)
			return text_node, []

		# Process coordinates if they exist for element nodes

		viewport_info = None

		if 'viewport' in node_data:
			viewport_info = ViewportInfo(
				width=node_data['viewport']['width'],
				height=node_data['viewport']['height'],
			)

		element_node = DOMElementNode(
			tag_name=node_data['tagName'],
			xpath=node_data['xpath'],
			attributes=node_data.get('attributes', {}),
			children=[],
			is_visible=node_data.get('isVisible', False),
			is_interactive=node_data.get('isInteractive', False),
			is_top_element=node_data.get('isTopElement', False),
			is_in_viewport=node_data.get('isInViewport', False),
			highlight_index=node_data.get('highlightIndex'),
			shadow_root=node_data.get('shadowRoot', False),
			parent=None,
			viewport_info=viewport_info,
		)

		children_ids = node_data.get('children', [])

		return element_node, children_ids
