# @file purpose: Serializes enhanced DOM trees to string format for LLM consumption


from dom.serializer.clickable_elements import ClickableElementDetector

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
		# Reset state
		self._interactive_counter = 1
		self._selector_map = {}
		self._semantic_groups = []

		# Step 1: Create simplified tree
		simplified_tree = self._create_simplified_tree(self.root_node)

		# Step 2: Optimize tree (remove unnecessary parents)
		optimized_tree = self._optimize_tree(simplified_tree)

		# # Step 3: Detect and group semantic elements
		# if optimized_tree:
		# 	self._detect_semantic_groups(optimized_tree)

		# Step 4: Assign interactive indices to clickable elements
		self._assign_interactive_indices_and_mark_new_nodes(optimized_tree)

		return SerializedDOMState(_root=optimized_tree, selector_map=self._selector_map)

	def _create_simplified_tree(self, node: EnhancedDOMTreeNode) -> SimplifiedNode | None:
		"""Step 1: Create a simplified tree with enhanced element detection."""
		if node.node_type == NodeType.DOCUMENT_NODE:
			if node.children_nodes:
				for child in node.children_nodes:
					simplified_child = self._create_simplified_tree(child)
					if simplified_child:
						return simplified_child
			return None

		elif node.node_type == NodeType.ELEMENT_NODE:
			if node.node_name == '#document':
				if node.children_nodes:
					for child in node.children_nodes:
						simplified_child = self._create_simplified_tree(child)
						if simplified_child:
							return simplified_child
				return None

			# Skip non-content elements
			if node.node_name.lower() in ['style', 'script', 'head', 'meta', 'link', 'title']:
				return None

			# Use enhanced scoring for inclusion decision
			is_interactive = ClickableElementDetector.is_interactive(node)
			is_visible = node.snapshot_node and node.snapshot_node.is_visible
			is_scrollable = node.is_scrollable

			# Include if interactive and visible, or scrollable, or has children to process
			should_include = (is_interactive and is_visible) or is_scrollable or node.children_nodes

			if should_include:
				simplified = SimplifiedNode(original_node=node)
				# simplified._analysis = analysis  # Store analysis for grouping

				# Process children
				if node.children_nodes:
					for child in node.children_nodes:
						simplified_child = self._create_simplified_tree(child)
						if simplified_child:
							simplified.children.append(simplified_child)

				# Return if meaningful or has meaningful children
				if (is_interactive and is_visible) or is_scrollable or simplified.children:
					return simplified

		elif node.node_type == NodeType.TEXT_NODE:
			# Include meaningful text nodes
			is_visible = node.snapshot_node and node.snapshot_node.is_visible
			if is_visible and node.node_value and node.node_value.strip() and len(node.node_value.strip()) > 1:
				return SimplifiedNode(original_node=node)

		return None

	def _optimize_tree(self, node: SimplifiedNode | None) -> SimplifiedNode | None:
		"""Step 2: Optimize tree structure."""
		if not node:
			return None

		# Process children
		optimized_children = []
		for child in node.children:
			optimized_child = self._optimize_tree(child)
			if optimized_child:
				optimized_children.append(optimized_child)

		node.children = optimized_children

		# Keep meaningful nodes
		if (
			ClickableElementDetector.is_interactive(node.original_node)
			or node.original_node.is_scrollable
			or node.original_node.node_type == NodeType.TEXT_NODE
			or node.children
		):
			return node

		return None

	def _collect_interactive_elements(self, node: SimplifiedNode, elements: list[SimplifiedNode]) -> None:
		"""Recursively collect interactive elements."""
		if ClickableElementDetector.is_interactive(node.original_node):
			elements.append(node)

		for child in node.children:
			self._collect_interactive_elements(child, elements)

	def _assign_interactive_indices_and_mark_new_nodes(self, node: SimplifiedNode | None) -> None:
		"""Assign interactive indices to clickable elements."""
		if not node:
			return

		# Assign index to clickable elements
		if ClickableElementDetector.is_interactive(node.original_node):
			node.interactive_index = self._interactive_counter
			self._selector_map[self._interactive_counter] = node.original_node
			self._interactive_counter += 1

			# Check if node is new
			if self._previous_cached_selector_map:
				previous_backend_node_ids = {node.backend_node_id for node in self._previous_cached_selector_map.values()}
				if node.original_node.backend_node_id not in previous_backend_node_ids:
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
			# Skip displaying nodes marked as should_display=False
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
					line = f'{depth_str}|SCROLL|<{node.original_node.tag_name}'
				elif node.interactive_index is not None:
					# Clickable (and possibly scrollable)
					new_prefix = '*' if node.is_new else ''
					scroll_prefix = '|SCROLL+' if node.original_node.is_scrollable else '['
					line = f'{depth_str}{new_prefix}{scroll_prefix}{node.interactive_index}]<{node.original_node.tag_name}'
				else:
					line = f'{depth_str}<{node.original_node.tag_name}'

				if attributes_html_str:
					line += f' {attributes_html_str}'

				line += ' />'
				formatted_text.append(line)

		elif node.original_node.node_type == NodeType.TEXT_NODE:
			# Include visible text
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
		role = node.ax_node.role if node.ax_node else None
		if role and node.node_name == role:
			attributes_to_include.pop('role', None)

		attrs_to_remove_if_text_matches = ['aria-label', 'placeholder', 'title']
		for attr in attrs_to_remove_if_text_matches:
			if attributes_to_include.get(attr) and attributes_to_include.get(attr, '').strip().lower() == text.strip().lower():
				del attributes_to_include[attr]

		if attributes_to_include:
			return ' '.join(f'{key}={cap_text_length(value, 15)}' for key, value in attributes_to_include.items())

		return ''
