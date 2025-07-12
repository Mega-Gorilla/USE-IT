from browser_use.dom.views import EnhancedDOMTreeNode, NodeType


class ClickableElementDetector:
	@staticmethod
	def is_interactive(node: EnhancedDOMTreeNode) -> bool:
		"""Check if this node is clickable/interactive using enhanced scoring."""

		# Skip non-element nodes
		if node.node_type != NodeType.ELEMENT_NODE:
			return False

		# Primary check: snapshot data from Chrome's heuristics
		if node.snapshot_node and node.snapshot_node.is_clickable:
			return True

		# remove html and body nodes
		if node.tag_name in {'html', 'body'}:
			return False

		# Secondary check: intrinsically interactive HTML elements
		interactive_tags = {'button', 'input', 'select', 'textarea', 'a', 'label', 'details', 'summary', 'option', 'optgroup'}
		if node.tag_name in interactive_tags:
			return True

		# Tertiary check: elements with interactive attributes
		if node.attributes:
			# Check for event handlers or interactive attributes
			interactive_attributes = {'onclick', 'onmousedown', 'onmouseup', 'onkeydown', 'onkeyup', 'tabindex'}
			if any(attr in node.attributes for attr in interactive_attributes):
				return True

			# Check for interactive ARIA roles
			if 'role' in node.attributes:
				interactive_roles = {
					'button',
					'link',
					'menuitem',
					'option',
					'radio',
					'checkbox',
					'tab',
					'textbox',
					'combobox',
					'slider',
					'spinbutton',
				}
				if node.attributes['role'] in interactive_roles:
					return True

		# Quaternary check: accessibility tree roles
		if node.ax_node and node.ax_node.role:
			interactive_ax_roles = {
				'button',
				'link',
				'menuitem',
				'option',
				'radio',
				'checkbox',
				'tab',
				'textbox',
				'combobox',
				'slider',
				'spinbutton',
				'listbox',
			}
			if node.ax_node.role in interactive_ax_roles:
				return True

		# Final check: cursor style indicates interactivity
		if node.snapshot_node and node.snapshot_node.cursor_style and node.snapshot_node.cursor_style == 'pointer':
			return True

		return False
