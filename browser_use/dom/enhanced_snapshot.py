"""
Enhanced snapshot processing for browser-use DOM tree extraction.

This module provides stateless functions for parsing Chrome DevTools Protocol (CDP) DOMSnapshot data
to extract visibility, clickability, cursor styles, and other layout information.
"""

from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns
from cdp_use.cdp.domsnapshot.types import (
	LayoutTreeSnapshot,
	NodeTreeSnapshot,
	RareBooleanData,
)

from browser_use.dom.views import DOMRect, EnhancedSnapshotNode

# Only the ESSENTIAL computed styles for interactivity and visibility detection
REQUIRED_COMPUTED_STYLES = [
	# Essential for visibility
	'display',
	'visibility',
	'opacity',
	'position',
	'z-index',
	'pointer-events',
	'cursor',
	'overflow',
]

# PERFORMANCE: Reduced from 19 to 8 styles (58% reduction)
# Removed non-essential styles: width, height, top, left, right, bottom, transform,
# clip, clip-path, user-select, background-color, color, border, margin, padding
# These can be computed from bounds/rects when actually needed

# Full style set for fallback scenarios
FULL_COMPUTED_STYLES = [
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


def build_snapshot_lookup(
	snapshot: CaptureSnapshotReturns,
	device_pixel_ratio: float = 1.0,
	viewport_bounds: dict | None = None,  # Your ±2000px viewport filtering
) -> dict[int, EnhancedSnapshotNode]:
	"""Build a lookup table of backend node ID to enhanced snapshot data with everything calculated upfront."""
	import logging

	logger = logging.getLogger(__name__)

	snapshot_lookup: dict[int, EnhancedSnapshotNode] = {}

	if not snapshot['documents']:
		logger.debug('🔍 SNAPSHOT: No documents in snapshot')
		return snapshot_lookup

	if viewport_bounds:
		logger.debug(
			f'🔍 SNAPSHOT: Using viewport bounds: top={viewport_bounds["top"]}, bottom={viewport_bounds["bottom"]}, height={viewport_bounds["total_height"]}'
		)
	else:
		logger.debug('🔍 SNAPSHOT: No viewport bounds - processing all elements')

	strings = snapshot['strings']

	for document in snapshot['documents']:
		nodes: NodeTreeSnapshot = document['nodes']
		layout: LayoutTreeSnapshot = document['layout']

		# Build backend node id to snapshot index lookup
		backend_node_to_snapshot_index = {}
		if 'backendNodeId' in nodes:
			for i, backend_node_id in enumerate(nodes['backendNodeId']):
				backend_node_to_snapshot_index[backend_node_id] = i

		# Build snapshot lookup for each backend node id
		total_nodes = len(backend_node_to_snapshot_index)
		processed_nodes = 0
		filtered_out_nodes = 0

		logger.debug(f'🔍 SNAPSHOT: Starting early filtering on {total_nodes} nodes...')
		import time

		processing_start = time.time()

		# PERFORMANCE: Pre-build layout index map to eliminate O(n²) double lookups
		layout_index_map = {}
		if layout and 'nodeIndex' in layout:
			for layout_idx, node_index in enumerate(layout['nodeIndex']):
				layout_index_map[node_index] = layout_idx

		layout_map_time = time.time() - processing_start
		logger.debug(f'🔍 SNAPSHOT: Built layout index map with {len(layout_index_map)} entries in {layout_map_time:.3f}s')

		for backend_node_id, snapshot_index in backend_node_to_snapshot_index.items():
			# PERFORMANCE OPTIMIZATION: Quick bounds check FIRST using cached layout map (O(1) lookup)
			should_skip = False
			if viewport_bounds and snapshot_index in layout_index_map:
				layout_idx = layout_index_map[snapshot_index]
				if layout_idx < len(layout.get('bounds', [])):
					bounds = layout['bounds'][layout_idx]
					if len(bounds) >= 4:
						# Quick viewport check using raw coordinates (device pixels)
						raw_x, raw_y, raw_width, raw_height = bounds[0], bounds[1], bounds[2], bounds[3]
						# Convert to CSS pixels for viewport comparison
						element_top = raw_y / device_pixel_ratio
						element_bottom = element_top + (raw_height / device_pixel_ratio)

						# Skip elements that don't intersect with viewport bounds
						if element_bottom < viewport_bounds['top'] or element_top > viewport_bounds['bottom']:
							filtered_out_nodes += 1
							should_skip = True

			if should_skip:
				continue  # Skip expensive processing entirely

			# Now do the expensive processing only for elements in viewport
			is_clickable = None
			if 'isClickable' in nodes:
				is_clickable = _parse_rare_boolean_data(nodes['isClickable'], snapshot_index)

			# Find corresponding layout node
			cursor_style = None
			is_visible = None
			bounding_box = None
			computed_styles = {}

			# PERFORMANCE: Use cached layout map instead of expensive enumerate loop
			paint_order = None
			client_rects = None
			scroll_rects = None
			stacking_contexts = None
			if snapshot_index in layout_index_map:
				layout_idx = layout_index_map[snapshot_index]
				if layout_idx < len(layout.get('bounds', [])):
					# Parse bounding box (we already did bounds check above for viewport filtering)
					bounds = layout['bounds'][layout_idx]
					if len(bounds) >= 4:
						# IMPORTANT: CDP coordinates are in device pixels, convert to CSS pixels
						# by dividing by the device pixel ratio
						raw_x, raw_y, raw_width, raw_height = bounds[0], bounds[1], bounds[2], bounds[3]

						# Apply device pixel ratio scaling to convert device pixels to CSS pixels
						bounding_box = DOMRect(
							x=raw_x / device_pixel_ratio,
							y=raw_y / device_pixel_ratio,
							width=raw_width / device_pixel_ratio,
							height=raw_height / device_pixel_ratio,
						)

					# Parse computed styles for this layout node
					if layout_idx < len(layout.get('styles', [])):
						style_indices = layout['styles'][layout_idx]
						computed_styles = _parse_computed_styles(strings, style_indices)
						cursor_style = computed_styles.get('cursor')

					# Extract paint order if available
					if layout_idx < len(layout.get('paintOrders', [])):
						paint_order = layout.get('paintOrders', [])[layout_idx]

					# Extract client rects if available
					client_rects_data = layout.get('clientRects', [])
					if layout_idx < len(client_rects_data):
						client_rect_data = client_rects_data[layout_idx]
						if client_rect_data and len(client_rect_data) >= 4:
							client_rects = DOMRect(
								x=client_rect_data[0],
								y=client_rect_data[1],
								width=client_rect_data[2],
								height=client_rect_data[3],
							)

					# Extract scroll rects if available
					scroll_rects_data = layout.get('scrollRects', [])
					if layout_idx < len(scroll_rects_data):
						scroll_rect_data = scroll_rects_data[layout_idx]
						if scroll_rect_data and len(scroll_rect_data) >= 4:
							scroll_rects = DOMRect(
								x=scroll_rect_data[0],
								y=scroll_rect_data[1],
								width=scroll_rect_data[2],
								height=scroll_rect_data[3],
							)

					# Extract stacking contexts if available
					if layout_idx < len(layout.get('stackingContexts', [])):
						stacking_contexts = layout.get('stackingContexts', {}).get('index', [])[layout_idx]

			snapshot_lookup[backend_node_id] = EnhancedSnapshotNode(
				is_clickable=is_clickable,
				cursor_style=cursor_style,
				bounds=bounding_box,
				clientRects=client_rects,
				scrollRects=scroll_rects,
				computed_styles=computed_styles if computed_styles else None,
				paint_order=paint_order,
				stacking_contexts=stacking_contexts,
			)
			processed_nodes += 1

	# Log filtering results with timing
	processing_end = time.time()
	processing_time = processing_end - processing_start

	logger.debug(
		f'🔍 SNAPSHOT: Processed {processed_nodes} nodes, skipped {filtered_out_nodes} nodes early (total: {total_nodes}) in {processing_time:.2f}s'
	)
	if viewport_bounds and total_nodes > 0:
		filter_percentage = (filtered_out_nodes / total_nodes) * 100
		process_percentage = (processed_nodes / total_nodes) * 100
		logger.debug(
			f'⚡ SNAPSHOT: Early viewport filtering skipped {filter_percentage:.1f}% of elements, processed only {process_percentage:.1f}%'
		)

		# Show performance improvement estimate
		if processed_nodes > 0:
			time_per_processed_node = processing_time / processed_nodes
			estimated_full_time = time_per_processed_node * total_nodes
			time_saved = estimated_full_time - processing_time
			logger.debug(
				f'⚡ SNAPSHOT: Estimated time saved: {time_saved:.2f}s (would have taken {estimated_full_time:.2f}s for all nodes)'
			)

	return snapshot_lookup
