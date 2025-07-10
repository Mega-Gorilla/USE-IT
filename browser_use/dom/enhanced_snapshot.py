"""
Enhanced snapshot processing for browser-use DOM tree extraction.

This module provides stateless functions for parsing Chrome DevTools Protocol (CDP) DOMSnapshot data
to extract visibility, clickability, cursor styles, and other layout information.
"""

from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns
from cdp_use.cdp.domsnapshot.types import (
	DocumentSnapshot,
	LayoutTreeSnapshot,
	NodeTreeSnapshot,
	RareBooleanData,
)

from browser_use.dom.views import EnhancedSnapshotNode

# Only the essential computed styles we actually need
REQUIRED_COMPUTED_STYLES = [
	'display',
	'visibility',
	'opacity',
	'position',
	'z-index',
	'pointer-events',
	'cursor',
	'overflow',
	'overflow-x',
	'overflow-y',
	'width',
	'height',
	'top',
	'left',
	'right',
	'bottom',
	'transform',
	'clip',
	'clip-path',
	'user-select',
	'background-color',
	'color',
	'border',
	'margin',
	'padding',
]


def _parse_rare_boolean_data(rare_data: RareBooleanData, index: int) -> bool | None:
	"""Parse rare boolean data from snapshot - returns True if index is in the rare data."""
	return index in rare_data['index']


def _parse_computed_styles(strings: list[str], style_indices: list[int]) -> dict[str, str]:
	"""Parse computed styles from layout tree using string indices."""
	styles = {}
	for i, style_index in enumerate(style_indices):
		if i < len(REQUIRED_COMPUTED_STYLES) and 0 <= style_index < len(strings):
			styles[REQUIRED_COMPUTED_STYLES[i]] = strings[style_index]
	return styles


def _is_element_visible(
	bounding_box: dict[str, float], computed_styles: dict[str, str], viewport_width: float, viewport_height: float
) -> bool:
	"""Determine if an element is visible. More permissive - considers elements visible if they start anywhere on the page."""
	# Check if element has zero dimensions
	if bounding_box['width'] <= 0 or bounding_box['height'] <= 0:
		return False

	# Check CSS visibility properties
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

	# More permissive visibility - element is visible if it has any presence on the page
	# and intersects with the viewport area (including elements that start at the beginning)
	elem_right = bounding_box['x'] + bounding_box['width']
	elem_bottom = bounding_box['y'] + bounding_box['height']

	# Element is visible if it has any intersection with the page area
	# This includes elements that start at x=0, y=0 (beginning of page)
	return elem_right > 0 and elem_bottom > 0 and bounding_box['x'] < viewport_width and bounding_box['y'] < viewport_height


def build_snapshot_lookup(
	snapshot: CaptureSnapshotReturns, viewport_width: float, viewport_height: float
) -> dict[int, EnhancedSnapshotNode]:
	"""Build a lookup table of backend node ID to enhanced snapshot data with everything calculated upfront."""
	snapshot_lookup: dict[int, EnhancedSnapshotNode] = {}

	if not snapshot['documents']:
		return snapshot_lookup

	document: DocumentSnapshot = snapshot['documents'][0]
	strings = snapshot['strings']
	nodes: NodeTreeSnapshot = document['nodes']
	layout: LayoutTreeSnapshot = document['layout']

	# Build backend node id to snapshot index lookup
	backend_node_to_snapshot_index = {}
	if 'backendNodeId' in nodes:
		for i, backend_node_id in enumerate(nodes['backendNodeId']):
			backend_node_to_snapshot_index[backend_node_id] = i

	# Build snapshot lookup for each backend node id
	for backend_node_id, snapshot_index in backend_node_to_snapshot_index.items():
		is_clickable = None
		if 'isClickable' in nodes:
			is_clickable = _parse_rare_boolean_data(nodes['isClickable'], snapshot_index)

		# Find corresponding layout node
		cursor_style = None
		is_visible = None
		bounding_box = None
		computed_styles = {}

		# Look for layout tree node that corresponds to this snapshot node
		for layout_idx, node_index in enumerate(layout.get('nodeIndex', [])):
			if node_index == snapshot_index and layout_idx < len(layout.get('bounds', [])):
				# Parse bounding box
				bounds = layout['bounds'][layout_idx]
				if len(bounds) >= 4:
					bounding_box = {'x': bounds[0], 'y': bounds[1], 'width': bounds[2], 'height': bounds[3]}

				# Parse computed styles for this layout node
				if layout_idx < len(layout.get('styles', [])):
					style_indices = layout['styles'][layout_idx]
					computed_styles = _parse_computed_styles(strings, style_indices)
					cursor_style = computed_styles.get('cursor')

				# Calculate visibility immediately if we have bounding box
				if bounding_box and computed_styles:
					is_visible = _is_element_visible(bounding_box, computed_styles, viewport_width, viewport_height)

				break

		snapshot_lookup[backend_node_id] = EnhancedSnapshotNode(
			is_clickable=is_clickable,
			cursor_style=cursor_style,
			is_visible=is_visible,
			bounding_box=bounding_box,
			computed_styles=computed_styles if computed_styles else None,
		)

	return snapshot_lookup
