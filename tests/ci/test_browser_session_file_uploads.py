"""
Comprehensive tests for browser session file upload element finding functionality.

Tests cover all codepaths in find_file_upload_element_by_index():
- Finding file inputs on current node, descendants, siblings, ancestors
- Parameter variations (max_height, max_descendant_depth)
- Edge cases and error handling
- Different DOM structures and nesting levels
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.dom.views import DOMElementNode


class TestBrowserSessionFileUploads:
	"""Tests for file upload element finding functionality."""

	@pytest.fixture
	async def browser_session(self):
		"""Create a BrowserSession instance for testing."""
		profile = BrowserProfile(headless=True, user_data_dir=None, keep_alive=False)
		session = BrowserSession(browser_profile=profile)

		# Mock the initialization check
		session._initialized = True

		yield session

		# Cleanup
		try:
			await session.stop()
		except Exception:
			pass

	def create_file_input_node(self, attributes: dict[str, str] | None = None) -> DOMElementNode:
		"""Create a file input DOM node for testing."""
		attrs = {'type': 'file'} if attributes is None else attributes
		return DOMElementNode(
			tag_name='input', xpath='//input[@type="file"]', attributes=attrs, children=[], is_visible=True, parent=None
		)

	def create_regular_input_node(self, input_type: str = 'text') -> DOMElementNode:
		"""Create a regular input DOM node for testing."""
		return DOMElementNode(
			tag_name='input',
			xpath=f'//input[@type="{input_type}"]',
			attributes={'type': input_type},
			children=[],
			is_visible=True,
			parent=None,
		)

	def create_div_node(self, children: list = None) -> DOMElementNode:
		"""Create a div DOM node for testing."""
		return DOMElementNode(tag_name='div', xpath='//div', attributes={}, children=children or [], is_visible=True, parent=None)

	def setup_parent_child_relationships(self, parent: DOMElementNode, children: list[DOMElementNode]):
		"""Set up bidirectional parent-child relationships."""
		parent.children = children
		for child in children:
			child.parent = parent

	async def test_find_file_input_on_current_node(self, browser_session):
		"""Test finding file input when the selected element itself is a file input."""
		# Create a file input node
		file_input = self.create_file_input_node()

		# Mock the selector map
		browser_session.get_selector_map = AsyncMock(return_value={0: file_input})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is file_input

	async def test_find_file_input_in_direct_child(self, browser_session):
		"""Test finding file input in direct children of selected element."""
		# Create DOM structure: div -> file_input
		file_input = self.create_file_input_node()
		parent_div = self.create_div_node()
		self.setup_parent_child_relationships(parent_div, [file_input])

		browser_session.get_selector_map = AsyncMock(return_value={0: parent_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is file_input

	async def test_find_file_input_in_nested_descendants(self, browser_session):
		"""Test finding file input in deeply nested descendants."""
		# Create DOM structure: div -> div -> div -> file_input
		file_input = self.create_file_input_node()
		inner_div = self.create_div_node()
		middle_div = self.create_div_node()
		outer_div = self.create_div_node()

		self.setup_parent_child_relationships(inner_div, [file_input])
		self.setup_parent_child_relationships(middle_div, [inner_div])
		self.setup_parent_child_relationships(outer_div, [middle_div])

		browser_session.get_selector_map = AsyncMock(return_value={0: outer_div})

		# Should find with default max_descendant_depth=3
		result = await browser_session.find_file_upload_element_by_index(0)
		assert result is file_input

		# Should not find with limited depth
		result = await browser_session.find_file_upload_element_by_index(0, max_descendant_depth=2)
		assert result is None

	async def test_find_file_input_in_sibling(self, browser_session):
		"""Test finding file input in sibling elements."""
		# Create DOM structure: parent -> [selected_div, file_input]
		selected_div = self.create_div_node()
		file_input = self.create_file_input_node()
		parent_div = self.create_div_node()

		self.setup_parent_child_relationships(parent_div, [selected_div, file_input])

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is file_input

	async def test_find_file_input_in_sibling_descendants(self, browser_session):
		"""Test finding file input in descendants of sibling elements."""
		# Create DOM structure: parent -> [selected_div, sibling_div -> file_input]
		selected_div = self.create_div_node()
		file_input = self.create_file_input_node()
		sibling_div = self.create_div_node()
		parent_div = self.create_div_node()

		self.setup_parent_child_relationships(sibling_div, [file_input])
		self.setup_parent_child_relationships(parent_div, [selected_div, sibling_div])

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is file_input

	async def test_find_file_input_via_ancestor_traversal(self, browser_session):
		"""Test finding file input by traversing up ancestors."""
		# Create DOM structure:
		# grandparent -> [parent -> selected_div, file_input]
		selected_div = self.create_div_node()
		parent_div = self.create_div_node()
		file_input = self.create_file_input_node()
		grandparent_div = self.create_div_node()

		self.setup_parent_child_relationships(parent_div, [selected_div])
		self.setup_parent_child_relationships(grandparent_div, [parent_div, file_input])

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is file_input

	async def test_max_height_parameter(self, browser_session):
		"""Test that max_height parameter limits ancestor traversal."""
		# Create deep DOM structure
		selected_div = self.create_div_node()
		level1_div = self.create_div_node()
		level2_div = self.create_div_node()
		level3_div = self.create_div_node()
		file_input = self.create_file_input_node()

		self.setup_parent_child_relationships(level1_div, [selected_div])
		self.setup_parent_child_relationships(level2_div, [level1_div])
		self.setup_parent_child_relationships(level3_div, [level2_div, file_input])

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		# Should find with sufficient max_height
		result = await browser_session.find_file_upload_element_by_index(0, max_height=3)
		assert result is file_input

		# Should not find with limited max_height
		result = await browser_session.find_file_upload_element_by_index(0, max_height=1)
		assert result is None

	async def test_multiple_file_inputs_returns_first_found(self, browser_session):
		"""Test that when multiple file inputs exist, the first one found is returned."""
		# Create structure with multiple file inputs
		file_input1 = self.create_file_input_node({'type': 'file', 'id': 'first'})
		file_input2 = self.create_file_input_node({'type': 'file', 'id': 'second'})
		selected_div = self.create_div_node()
		parent_div = self.create_div_node()

		# First input is in descendants, second is in siblings
		self.setup_parent_child_relationships(selected_div, [file_input1])
		self.setup_parent_child_relationships(parent_div, [selected_div, file_input2])

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		# Should return the first one found (in descendants)
		assert result is file_input1

	async def test_index_not_in_selector_map(self, browser_session):
		"""Test behavior when index is not found in selector map."""
		browser_session.get_selector_map = AsyncMock(return_value={})

		result = await browser_session.find_file_upload_element_by_index(999)

		assert result is None

	async def test_no_file_input_found(self, browser_session):
		"""Test behavior when no file input exists in the DOM structure."""
		# Create structure with only regular inputs
		text_input = self.create_regular_input_node('text')
		button_input = self.create_regular_input_node('button')
		selected_div = self.create_div_node()
		parent_div = self.create_div_node()

		self.setup_parent_child_relationships(selected_div, [text_input])
		self.setup_parent_child_relationships(parent_div, [selected_div, button_input])

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is None

	async def test_file_input_case_insensitive(self, browser_session):
		"""Test that file input detection is case insensitive."""
		# Test various case combinations
		test_cases = [
			{'type': 'FILE'},
			{'type': 'File'},
			{'TYPE': 'file'},
			{'Type': 'File'},
		]

		for i, attrs in enumerate(test_cases):
			file_input = DOMElementNode(
				tag_name='INPUT',  # Also test uppercase tag
				xpath='//input',
				attributes=attrs,
				children=[],
				is_visible=True,
				parent=None,
			)

			browser_session.get_selector_map = AsyncMock(return_value={i: file_input})

			result = await browser_session.find_file_upload_element_by_index(i)
			assert result is file_input

	async def test_missing_type_attribute(self, browser_session):
		"""Test behavior with input elements missing type attribute."""
		# Input without type attribute (should default to text)
		input_no_type = DOMElementNode(
			tag_name='input',
			xpath='//input',
			attributes={},  # No type attribute
			children=[],
			is_visible=True,
			parent=None,
		)

		browser_session.get_selector_map = AsyncMock(return_value={0: input_no_type})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is None

	async def test_non_input_elements_ignored(self, browser_session):
		"""Test that non-input elements are properly ignored."""
		# Create elements that might have type="file" but aren't input tags
		fake_file_div = DOMElementNode(
			tag_name='div',
			xpath='//div',
			attributes={'type': 'file'},  # Wrong tag but has type=file
			children=[],
			is_visible=True,
			parent=None,
		)

		browser_session.get_selector_map = AsyncMock(return_value={0: fake_file_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is None

	async def test_exception_handling(self, browser_session):
		"""Test that exceptions are properly caught and logged."""
		# Mock get_selector_map to raise an exception
		browser_session.get_selector_map = AsyncMock(side_effect=Exception('Test exception'))

		# Mock logger and get_current_page
		browser_session.logger = MagicMock()
		browser_session.get_current_page = AsyncMock(return_value=MagicMock(url='http://test.com'))

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is None
		browser_session.logger.debug.assert_called_once()

	async def test_zero_parameters(self, browser_session):
		"""Test behavior with zero values for parameters."""
		# Create simple structure where file input is in descendant
		file_input = self.create_file_input_node()
		child_div = self.create_div_node()
		parent_div = self.create_div_node()

		self.setup_parent_child_relationships(child_div, [file_input])
		self.setup_parent_child_relationships(parent_div, [child_div])

		browser_session.get_selector_map = AsyncMock(return_value={0: parent_div})

		# With max_descendant_depth=0, should not find in descendants
		result = await browser_session.find_file_upload_element_by_index(0, max_descendant_depth=0)
		assert result is None

		# With max_height=0, should not traverse up ancestors
		result = await browser_session.find_file_upload_element_by_index(0, max_height=0)
		assert result is None

	async def test_reached_root_without_parent(self, browser_session):
		"""Test behavior when traversal reaches root element (no parent)."""
		# Create structure where selected element has no parent
		selected_div = self.create_div_node()
		selected_div.parent = None  # Root element

		browser_session.get_selector_map = AsyncMock(return_value={0: selected_div})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is None

	async def test_complex_mixed_structure(self, browser_session):
		"""Test a complex DOM structure with mixed element types."""
		# Create a realistic form structure
		# form -> div -> [label, div -> [text_input, file_input]]
		text_input = self.create_regular_input_node('text')
		file_input = self.create_file_input_node()
		label = DOMElementNode(
			tag_name='label', xpath='//label', attributes={'for': 'upload'}, children=[], is_visible=True, parent=None
		)

		input_container = self.create_div_node()
		form_row = self.create_div_node()
		form_element = DOMElementNode(tag_name='form', xpath='//form', attributes={}, children=[], is_visible=True, parent=None)

		# Build the structure
		self.setup_parent_child_relationships(input_container, [text_input, file_input])
		self.setup_parent_child_relationships(form_row, [label, input_container])
		self.setup_parent_child_relationships(form_element, [form_row])

		# Select the label
		browser_session.get_selector_map = AsyncMock(return_value={0: label})

		result = await browser_session.find_file_upload_element_by_index(0)

		assert result is file_input
