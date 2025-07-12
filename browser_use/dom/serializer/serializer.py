# @file purpose: Serializes enhanced DOM trees to string format for LLM consumption

from dataclasses import dataclass, field
from enum import Enum

from browser_use.dom.utils import cap_text_length
from browser_use.dom.views import DOMSelectorMap, EnhancedDOMTreeNode, NodeType, SerializedDOMState, SimplifiedNode


class ElementGroup(Enum):
	"""Types of element groups for semantic organization."""

	FORM = 'FORM'
	NAVIGATION = 'NAVIGATION'
	DROPDOWN = 'DROPDOWN'
	MENU = 'MENU'
	TABLE = 'TABLE'
	LIST = 'LIST'
	TABS = 'TABS'
	CONTENT = 'CONTENT'


@dataclass
class ElementAnalysis:
	"""Analysis of element interactivity with scoring."""

	score: int
	confidence: str
	primary_reason: str
	element_type: str
	evidence: list[str] = field(default_factory=list)
	warnings: list[str] = field(default_factory=list)
	interactive_indicators: dict[str, bool] = field(default_factory=dict)

	@classmethod
	def analyze_element_interactivity(cls, node: EnhancedDOMTreeNode) -> 'ElementAnalysis':
		"""Analyze element interactivity with comprehensive scoring."""
		element_name = node.node_name.upper()
		attributes = node.attributes or {}

		score = 0
		evidence = []
		warnings = []
		element_category = 'unknown'

		# Enhanced button detection indicators
		button_indicators = [
			'btn',
			'button',
			'click',
			'submit',
			'action',
			'trigger',
			'toggle',
			'press',
			'tap',
			'select',
			'choose',
			'confirm',
			'cancel',
			'ok',
			'yes',
			'no',
			'close',
			'open',
			'show',
			'hide',
			'expand',
			'collapse',
			'menu',
			'dropdown',
			'popup',
			'modal',
			'dialog',
			'tab',
			'nav',
			'link',
			'item',
			'option',
			'choice',
		]

		# TIER 1: HIGHEST PRIORITY (90-100 points) - Core interactive elements
		if element_name in ['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA']:
			element_category = 'form_control'
			score += 90
			evidence.append(f'HIGH PRIORITY: Core form element: {element_name}')

			if attributes.get('type'):
				input_type = attributes['type'].lower()
				evidence.append(f'Input type: {input_type}')
				if input_type in ['submit', 'button', 'reset']:
					score += 10
				elif input_type in ['text', 'email', 'password', 'search', 'tel', 'url']:
					score += 8
				elif input_type in ['checkbox', 'radio']:
					score += 6

			if attributes.get('disabled') == 'true':
				score = max(25, score - 40)
				warnings.append('Element is disabled but still detectable')

		# TIER 2: VERY HIGH PRIORITY (80-89 points) - Cursor pointer + interactive elements
		elif cls._has_cursor_pointer(node):
			element_category = 'cursor_pointer'
			score += 80
			evidence.append('VERY HIGH PRIORITY: Element has cursor: pointer')

			if element_name in ['DIV', 'SPAN', 'A', 'LI', 'TD', 'TH']:
				score += 5
				evidence.append('Meaningful element with cursor pointer')

		# TIER 3: HIGH PRIORITY (70-79 points) - Links and strong event indicators
		elif element_name == 'A':
			element_category = 'link'
			score += 70
			if attributes.get('href'):
				href = attributes['href']
				score += 10
				evidence.append(f'HIGH PRIORITY: Link with href: {href[:50]}...' if len(href) > 50 else f'Link with href: {href}')

				if href.startswith(('http://', 'https://')):
					score += 5
					evidence.append('External link')
				elif href.startswith('/'):
					score += 4
					evidence.append('Internal absolute link')
			else:
				score += 8
				evidence.append('Link element without href (likely interactive)')

		# ENHANCED ONCLICK DETECTION - TIER 3 priority
		elif 'onclick' in attributes:
			element_category = 'onclick_handler'
			score += 75
			evidence.append('HIGH PRIORITY: Has onclick event handler')

		# TIER 4: MEDIUM-HIGH PRIORITY (50-69 points) - ARIA roles and containers
		elif attributes.get('role'):
			if element_category == 'unknown':
				element_category = 'aria_role'
			role = attributes['role'].lower()
			interactive_roles = {
				'button': 65,
				'link': 65,
				'menuitem': 60,
				'tab': 60,
				'option': 55,
				'checkbox': 55,
				'radio': 55,
				'switch': 55,
				'slider': 50,
				'spinbutton': 50,
				'combobox': 50,
				'textbox': 50,
			}

			if role in interactive_roles:
				role_score = interactive_roles[role]
				score += role_score
				evidence.append(f'MEDIUM-HIGH PRIORITY: ARIA role: {role} (+{role_score})')

		# ENHANCED BUTTON DETECTION IN CONTAINERS - TIER 4-5 priority
		elif element_name in ['DIV', 'SPAN', 'LI', 'TD', 'TH', 'SECTION', 'ARTICLE']:
			if element_category == 'unknown':
				element_category = 'container'

			container_score = cls._analyze_container_interactivity(node, attributes, button_indicators, evidence)
			score += container_score

		# ENHANCED TABINDEX ANALYSIS
		if 'tabindex' in attributes:
			try:
				tabindex = int(attributes['tabindex'])
				if tabindex >= 0:
					score += 25
					evidence.append(f'ENHANCED: Focusable (tabindex: {tabindex}) (+25)')
				elif tabindex == -1:
					score += 15
					evidence.append('Programmatically focusable (tabindex: -1) (+15)')
			except ValueError:
				warnings.append(f'Invalid tabindex: {attributes["tabindex"]}')

		# ACCESSIBILITY TREE ENHANCEMENTS
		accessibility_boost = cls._analyze_accessibility_properties(node, evidence)
		score += accessibility_boost

		# DETERMINE FINAL CONFIDENCE
		if score >= 85:
			confidence = 'DEFINITE'
		elif score >= 65:
			confidence = 'LIKELY'
		elif score >= 40:
			confidence = 'POSSIBLE'
		elif score >= 20:
			confidence = 'QUESTIONABLE'
		else:
			confidence = 'MINIMAL'

		primary_reason = element_category if element_category != 'unknown' else 'mixed_indicators'

		# ENHANCED INTERACTIVE INDICATORS
		interactive_indicators = {
			'has_onclick': 'onclick' in attributes,
			'has_href': 'href' in attributes,
			'has_tabindex': 'tabindex' in attributes,
			'has_role': 'role' in attributes,
			'has_aria_label': 'aria-label' in attributes,
			'has_data_attrs': any(k.startswith('data-') for k in attributes.keys()),
			'has_button_classes': any(indicator in attributes.get('class', '').lower() for indicator in button_indicators),
			'has_pointer_cursor': cls._has_cursor_pointer(node),
			'has_event_handlers': any(k.startswith('on') for k in attributes.keys()),
			'is_focusable': cls._is_ax_focusable(node),
		}

		return cls(
			score=score,
			confidence=confidence,
			primary_reason=primary_reason,
			element_type=element_name,
			evidence=evidence,
			warnings=warnings,
			interactive_indicators=interactive_indicators,
		)

	@classmethod
	def _has_cursor_pointer(cls, node: EnhancedDOMTreeNode) -> bool:
		"""Enhanced cursor pointer detection."""
		if node.snapshot_node:
			if getattr(node.snapshot_node, 'cursor_style', None) == 'pointer':
				return True
			if hasattr(node.snapshot_node, 'computed_styles'):
				styles = node.snapshot_node.computed_styles or {}
				if styles.get('cursor') == 'pointer':
					return True
		return False

	@classmethod
	def _analyze_container_interactivity(
		cls, node: EnhancedDOMTreeNode, attributes: dict[str, str], button_indicators: list[str], evidence: list[str]
	) -> int:
		"""Analyze container elements for interactivity."""
		container_score = 10

		# Enhanced CSS class analysis
		css_classes = attributes.get('class', '').lower()
		button_score_boost = 0
		for indicator in button_indicators:
			if indicator in css_classes:
				button_score_boost += 20
				evidence.append(f'Button-like class: {indicator}')

		container_score += button_score_boost

		# Check for cursor pointer
		if cls._has_cursor_pointer(node):
			container_score += 50
			evidence.append('Container with cursor pointer (+50)')

		# Enhanced ARIA attribute detection
		interactive_aria = [
			'aria-label',
			'aria-expanded',
			'aria-selected',
			'aria-pressed',
			'aria-checked',
			'aria-controls',
			'aria-haspopup',
			'aria-live',
			'aria-hidden',
		]
		found_aria = [attr for attr in interactive_aria if attr in attributes]
		if found_aria:
			aria_boost = len(found_aria) * 8
			container_score += aria_boost
			evidence.append(f'Interactive ARIA attributes: {", ".join(found_aria)} (+{aria_boost})')

		return container_score

	@classmethod
	def _analyze_accessibility_properties(cls, node: EnhancedDOMTreeNode, evidence: list[str]) -> int:
		"""Analyze accessibility properties for enhanced scoring."""
		score_boost = 0

		if cls._is_ax_focusable(node):
			score_boost += 200  # ALWAYS detected - score guaranteed to exceed any threshold
			evidence.append('ALWAYS DETECTED: Accessibility tree marked as focusable (+200)')

		if node.ax_node and node.ax_node.role:
			role = node.ax_node.role.lower()
			interactive_ax_roles = {
				'button': 25,
				'link': 25,
				'menuitem': 20,
				'tab': 20,
				'checkbox': 20,
				'radio': 20,
				'slider': 15,
				'textbox': 15,
			}
			if role in interactive_ax_roles:
				boost = interactive_ax_roles[role]
				score_boost += boost
				evidence.append(f'Interactive AX role: {role} (+{boost})')

		return score_boost

	@classmethod
	def _is_ax_focusable(cls, node: EnhancedDOMTreeNode) -> bool:
		"""Check if element is focusable according to accessibility tree."""
		if node.ax_node and node.ax_node.properties:
			for prop in node.ax_node.properties:
				if prop.name == 'focusable' and prop.value:
					return True
		return False


@dataclass
class SemanticGroup:
	"""A semantic group of related elements."""

	group_type: ElementGroup
	elements: list['SimplifiedNode'] = field(default_factory=list)
	title: str | None = None


class ClickableElementDetector:
	@staticmethod
	def is_interactive(node: EnhancedDOMTreeNode) -> bool:
		"""Check if this node is clickable/interactive using enhanced scoring."""
		analysis = ElementAnalysis.analyze_element_interactivity(node)
		# Use a threshold of 20 points for interactivity
		return analysis.score >= 50


class DOMTreeSerializer:
	"""Serializes enhanced DOM trees to string format."""

	def __init__(self, root_node: EnhancedDOMTreeNode, previous_cached_state: SerializedDOMState | None = None):
		self.root_node = root_node
		self._interactive_counter = 1
		self._selector_map: DOMSelectorMap = {}
		self._previous_cached_selector_map = previous_cached_state.selector_map if previous_cached_state else None
		self._semantic_groups: list[SemanticGroup] = []

	def serialize_accessible_elements(self) -> SerializedDOMState:
		# Reset state
		self._interactive_counter = 1
		self._selector_map = {}
		self._semantic_groups = []

		# Step 1: Create simplified tree
		simplified_tree = self._create_simplified_tree(self.root_node)

		# Step 2: Optimize tree (remove unnecessary parents)
		optimized_tree = self._optimize_tree(simplified_tree)

		# Step 3: Detect and group semantic elements
		if optimized_tree:
			self._detect_semantic_groups(optimized_tree)

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
			analysis = ElementAnalysis.analyze_element_interactivity(node)
			is_interactive = analysis.score >= 20
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

	def _detect_semantic_groups(self, node: SimplifiedNode) -> None:
		"""Step 3: Detect semantic groups of related elements."""
		self._semantic_groups = []

		# Collect all interactive elements
		interactive_elements = []
		self._collect_interactive_elements(node, interactive_elements)

		# Group by semantic type
		form_elements = []
		nav_elements = []
		dropdown_elements = []
		list_elements = []
		tab_elements = []
		other_elements = []

		for element in interactive_elements:
			group_type = self._determine_element_group_type(element)
			if group_type == ElementGroup.FORM:
				form_elements.append(element)
			elif group_type == ElementGroup.NAVIGATION:
				nav_elements.append(element)
			elif group_type == ElementGroup.DROPDOWN:
				dropdown_elements.append(element)
			elif group_type == ElementGroup.LIST:
				list_elements.append(element)
			elif group_type == ElementGroup.TABS:
				tab_elements.append(element)
			else:
				other_elements.append(element)

		# Create semantic groups
		if form_elements:
			self._semantic_groups.append(SemanticGroup(ElementGroup.FORM, form_elements, 'Form Elements'))
		if nav_elements:
			self._semantic_groups.append(SemanticGroup(ElementGroup.NAVIGATION, nav_elements, 'Navigation'))
		if dropdown_elements:
			self._semantic_groups.append(SemanticGroup(ElementGroup.DROPDOWN, dropdown_elements, 'Dropdown/Menu'))
		if list_elements:
			self._semantic_groups.append(SemanticGroup(ElementGroup.LIST, list_elements, 'List Items'))
		if tab_elements:
			self._semantic_groups.append(SemanticGroup(ElementGroup.TABS, tab_elements, 'Tabs'))
		if other_elements:
			self._semantic_groups.append(SemanticGroup(ElementGroup.CONTENT, other_elements, 'Interactive Content'))

	def _collect_interactive_elements(self, node: SimplifiedNode, elements: list[SimplifiedNode]) -> None:
		"""Recursively collect interactive elements."""
		if ClickableElementDetector.is_interactive(node.original_node):
			elements.append(node)

		for child in node.children:
			self._collect_interactive_elements(child, elements)

	def _determine_element_group_type(self, element: SimplifiedNode) -> ElementGroup:
		"""Determine the semantic group type for an element."""
		node = element.original_node
		node_name = node.node_name.upper()
		attrs = node.attributes or {}

		# Form elements
		if node_name in ['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'FORM']:
			return ElementGroup.FORM

		# Navigation elements
		if node_name == 'A' and attrs.get('href'):
			href = attrs['href']
			if not href.startswith(('mailto:', 'tel:', 'javascript:', '#')):
				return ElementGroup.NAVIGATION

		# Check for semantic indicators in classes/roles
		classes = attrs.get('class', '').lower()
		role = attrs.get('role', '').lower()

		# Dropdown/Menu indicators
		if any(indicator in classes for indicator in ['dropdown', 'menu', 'select']):
			return ElementGroup.DROPDOWN
		if role in ['menu', 'menuitem', 'option']:
			return ElementGroup.DROPDOWN

		# List indicators
		if node_name in ['LI', 'UL', 'OL'] or 'list' in classes:
			return ElementGroup.LIST

		# Tab indicators
		if 'tab' in classes or role == 'tab':
			return ElementGroup.TABS

		return ElementGroup.CONTENT

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
