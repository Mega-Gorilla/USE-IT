# @file purpose: Serializes enhanced DOM trees to string format for LLM consumption


from cdp_use.cdp.accessibility.types import AXPropertyName

from browser_use.dom.utils import cap_text_length
from browser_use.dom.views import DOMSelectorMap, EnhancedDOMTreeNode, NodeType, SerializedDOMState, SimplifiedNode


class DOMTreeSerializer:
	"""Serializes enhanced DOM trees to string format."""

	def __init__(self, root_node: EnhancedDOMTreeNode, previous_cached_state: SerializedDOMState | None = None):
		self.root_node = root_node

		self._interactive_counter = 1
		self._selector_map: DOMSelectorMap = {}

		self._previous_cached_selector_map = previous_cached_state.selector_map if previous_cached_state else None

	def serialize_accessible_elements(self) -> SerializedDOMState:
		"""Convert the enhanced DOM tree to string format, showing accessible elements and text content.


		Returns:
			- Serialized string representation
			- Selector map mapping interactive indices to DOM nodes
		"""

		# Reset state
		self._interactive_counter = 1
		self._selector_map = {}

		# Step 1: Create simplified tree
		simplified_tree = self._create_simplified_tree(self.root_node)

		# Step 2: Optimize tree (remove unnecessary parents)
		optimized_tree = self._optimize_tree(simplified_tree)

		# Step 3: Assign interactive indices to clickable elements
		self._assign_interactive_indices_and_mark_new_nodes(optimized_tree)

		return SerializedDOMState(_root=optimized_tree, selector_map=self._selector_map)

	def _create_simplified_tree(self, node: EnhancedDOMTreeNode) -> SimplifiedNode | None:
		"""Step 1: Create a simplified tree with relevant elements."""

		if node.node_type == NodeType.DOCUMENT_NODE:
			# Document nodes - process children directly and return the first meaningful child
			if node.children_nodes:
				for child in node.children_nodes:
					simplified_child = self._create_simplified_tree(child)
					if simplified_child:
						return simplified_child
			return None

		elif node.node_type == NodeType.ELEMENT_NODE:
			# Skip #document nodes entirely - process children directly
			if node.node_name == '#document':
				if node.children_nodes:
					for child in node.children_nodes:
						simplified_child = self._create_simplified_tree(child)
						if simplified_child:
							return simplified_child
				return None

			# Skip elements that contain non-content
			if node.node_name.lower() in ['style', 'script', 'head', 'meta', 'link', 'title']:
				return None

			# Be more inclusive - include most elements and let optimization handle filtering
			# Include if: clickable, has AX data (non-ignored), or structural element, or has meaningful attributes

			has_focusable_property = (
				any(property.name == AXPropertyName.FOCUSABLE and property.value for property in node.ax_node.properties)
				if node.ax_node and node.ax_node.properties
				else False
			)

			cursor_pointer = node.snapshot_node and node.snapshot_node.cursor_style == 'pointer'

			is_clickable = node.snapshot_node and node.snapshot_node.is_clickable or has_focusable_property or cursor_pointer

			is_visible = node.snapshot_node and node.snapshot_node.is_visible
			is_scrollable = node.is_scrollable

			# Simple criteria: include if (clickable AND visible) OR scrollable
			should_include = (is_clickable and is_visible) or is_scrollable

			if should_include or node.children_nodes:  # Include if meaningful OR has children to process
				simplified = SimplifiedNode(original_node=node)

				# Process children
				if node.children_nodes:
					for child in node.children_nodes:
						simplified_child = self._create_simplified_tree(child)
						if simplified_child:
							simplified.children.append(simplified_child)

				# Only return this node if it's meaningful OR has meaningful children
				if should_include or simplified.children:
					return simplified
				return None

		elif node.node_type == NodeType.TEXT_NODE:
			# Include text nodes only if visible
			is_visible = node.snapshot_node and node.snapshot_node.is_visible
			if is_visible and node.node_value and node.node_value.strip() and len(node.node_value.strip()) > 1:
				return SimplifiedNode(original_node=node)
			return None

		# Skip other node types (COMMENT_NODE, DOCUMENT_TYPE_NODE, etc.)
		return None

	def _optimize_tree(self, node: SimplifiedNode | None) -> SimplifiedNode | None:
		"""Step 2: Simple optimization - just process children and keep meaningful nodes."""
		if not node:
			return None

		# Process all children
		optimized_children = []
		for child in node.children:
			optimized_child = self._optimize_tree(child)
			if optimized_child:
				optimized_children.append(optimized_child)

		# Update children with optimized versions
		node.children = optimized_children

		# Keep the node if it's meaningful or has meaningful children
		if (
			node.is_clickable()
			or node.original_node.is_scrollable
			or node.original_node.node_type == NodeType.TEXT_NODE
			or node.children
		):
			return node

		# Remove empty nodes
		return None

	def _assign_interactive_indices_and_mark_new_nodes(self, node: SimplifiedNode | None) -> None:
		"""Assign interactive indices to clickable elements."""
		if not node:
			return

		# Assign index to clickable elements
		if node.is_clickable():
			node.interactive_index = self._interactive_counter
			self._selector_map[self._interactive_counter] = node.original_node
			self._interactive_counter += 1

			# If we provided previous cached selector map, check if the node is new
			# TODO: maybe we can also check for more than just 1 step
			previous_backend_node_ids: set[int] | None = set()
			if self._previous_cached_selector_map:
				previous_backend_node_ids = {node.backend_node_id for node in self._previous_cached_selector_map.values()}

			if previous_backend_node_ids:
				if node.original_node.backend_node_id in previous_backend_node_ids:
					node.is_new = True

		# Process children
		for child in node.children:
			self._assign_interactive_indices_and_mark_new_nodes(child)

	@staticmethod
	def serialize_tree(node: SimplifiedNode | None, include_attributes: list[str], depth: int = 0) -> str:
		"""Serialize the optimized tree to string format."""
		if not node:
			return ''

		formatted_text = []
		depth_str = depth * '\t'
		next_depth = depth

		if node.original_node.node_type == NodeType.ELEMENT_NODE:
			# Skip displaying nodes marked as should_display=False (virtual nodes)
			if not node.should_display:
				for child in node.children:
					child_text = DOMTreeSerializer.serialize_tree(child, include_attributes, depth)
					if child_text:
						formatted_text.append(child_text)
				return '\n'.join(formatted_text)

			# Add element with interactive_index if clickable or scrollable
			if node.interactive_index is not None or node.original_node.is_scrollable:
				next_depth += 1

				# Build attributes string
				attributes_html_str = DOMTreeSerializer._build_attributes_string(node.original_node, include_attributes, '')

				# Build the line
				if node.original_node.is_scrollable and node.interactive_index is None:
					# Scrollable but not clickable
					line = f'{depth_str}|SCROLL|<{node.original_node.node_name}'
				elif node.interactive_index is not None:
					# Clickable (and possibly scrollable)
					new_prefix = '*' if node.is_new else ''
					scroll_prefix = '|SCROLL+' if node.original_node.is_scrollable else '['
					line = f'{depth_str}{scroll_prefix}{new_prefix}{node.interactive_index}]<{node.original_node.node_name}'
				else:
					line = f'{depth_str}<{node.original_node.node_name}'

				if attributes_html_str:
					line += f' {attributes_html_str}'

				line += ' />'
				formatted_text.append(line)

		elif node.original_node.node_type == NodeType.TEXT_NODE:
			# Include all visible text where it naturally appears
			is_visible = node.original_node.snapshot_node and node.original_node.snapshot_node.is_visible
			if (
				is_visible
				and node.original_node.node_value
				and node.original_node.node_value.strip()
				and len(node.original_node.node_value.strip()) > 1
			):
				clean_text = node.original_node.node_value.strip()
				formatted_text.append(f'{depth_str}{clean_text}')

		# Process children
		for child in node.children:
			child_text = DOMTreeSerializer.serialize_tree(child, include_attributes, next_depth)
			if child_text:
				formatted_text.append(child_text)

		return '\n'.join(formatted_text)

	@staticmethod
	def _build_attributes_string(node: EnhancedDOMTreeNode, include_attributes: list[str], text: str) -> str:
		"""Build the attributes string for an element."""
		if not node.attributes:
			return ''

		attributes_to_include = {
			key: str(value).strip()
			for key, value in node.attributes.items()
			if key in include_attributes and str(value).strip() != ''
		}

		# Remove duplicate values
		ordered_keys = [key for key in include_attributes if key in attributes_to_include]

		if len(ordered_keys) > 1:
			keys_to_remove = set()
			seen_values = {}

			for key in ordered_keys:
				value = attributes_to_include[key]
				if len(value) > 5:
					if value in seen_values:
						keys_to_remove.add(key)
					else:
						seen_values[value] = key

			for key in keys_to_remove:
				del attributes_to_include[key]

		# Remove attributes that duplicate accessibility data
		role = DOMTreeSerializer._get_accessibility_role(node)
		if role and node.node_name == role:
			attributes_to_include.pop('role', None)

		attrs_to_remove_if_text_matches = ['aria-label', 'placeholder', 'title']
		for attr in attrs_to_remove_if_text_matches:
			if attributes_to_include.get(attr) and attributes_to_include.get(attr, '').strip().lower() == text.strip().lower():
				del attributes_to_include[attr]

		if attributes_to_include:
			return ' '.join(f'{key}={cap_text_length(value, 15)}' for key, value in attributes_to_include.items())

		return ''

	@staticmethod
	def _get_accessibility_role(node: EnhancedDOMTreeNode) -> str | None:
		"""Get the accessibility role from the AX node."""
		if node.ax_node:
			return node.ax_node.role
		return None
