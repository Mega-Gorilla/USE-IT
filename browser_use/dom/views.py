from dataclasses import asdict, dataclass
from enum import Enum

from cdp_use.cdp.accessibility.types import AXPropertyName
from cdp_use.cdp.dom.types import ShadowRootType
from pydantic import BaseModel

################## TO BE REMOVED
DEFAULT_INCLUDE_ATTRIBUTES = [
	'title',
	'type',
	'checked',
	'name',
	'role',
	'value',
	'placeholder',
	'data-date-format',
	'alt',
	'aria-label',
	'aria-expanded',
	'data-state',
	'aria-checked',
]


class DOMNodeAttributes(BaseModel):
	name: str
	value: str


################## END TO BE REMOVED


class NodeType(int, Enum):
	"""DOM node types based on the DOM specification."""

	ELEMENT_NODE = 1
	ATTRIBUTE_NODE = 2
	TEXT_NODE = 3
	CDATA_SECTION_NODE = 4
	ENTITY_REFERENCE_NODE = 5
	ENTITY_NODE = 6
	PROCESSING_INSTRUCTION_NODE = 7
	COMMENT_NODE = 8
	DOCUMENT_NODE = 9
	DOCUMENT_TYPE_NODE = 10
	DOCUMENT_FRAGMENT_NODE = 11
	NOTATION_NODE = 12


# @dataclass
# class EnchancedAXRelatedNode:
# 	node: 'EnhancedDOMTreeNode'
# 	text: str | None


@dataclass(slots=True)
class EnhancedAXProperty:
	"""we don't need `sources` and `related_nodes` for now (not sure how to use them)

	TODO: there is probably some way to determine whether it has a value or related nodes or not, but for now it's kinda fine idk
	"""

	name: AXPropertyName
	value: str | bool | None
	# related_nodes: list[EnchancedAXRelatedNode] | None


@dataclass(slots=True)
class EnhancedAXNode:
	ax_node_id: str
	"""Not to be confused the DOM node_id. Only useful for AX node tree"""
	ignored: bool
	# we don't need ignored_reasons as we anyway ignore the node otherwise
	role: str | None
	name: str | None
	description: str | None

	properties: list[EnhancedAXProperty] | None


@dataclass(slots=True)
class EnhancedSnapshotNode:
	"""Snapshot data extracted from DOMSnapshot for enhanced functionality."""

	is_clickable: bool | None
	is_visible: bool | None
	cursor_style: str | None
	bounding_box: dict[str, float] | None
	"""Bounding box with x, y, width, height in viewport coordinates"""
	computed_styles: dict[str, str] | None
	"""Computed styles from the layout tree"""


@dataclass(slots=True)
class EnhancedDOMTreeNode:
	"""
	Enchanced DOM tree node that contains information from AX, DOM, and Snapshot trees. It's mostly based on the types on DOM node type with enchanced data from AX and Snapshot trees.

	@dev when serializing check if the value is a valid value first!

	Learn more about the fields:
	- (DOM node) https://chromedevtools.github.io/devtools-protocol/tot/DOM/#type-BackendNode
	- (AX node) https://chromedevtools.github.io/devtools-protocol/tot/Accessibility/#type-AXNode
	- (Snapshot node) https://chromedevtools.github.io/devtools-protocol/tot/DOMSnapshot/#type-DOMNode
	"""

	# region - DOM Node data
	node_id: int
	backend_node_id: int

	node_type: NodeType
	"""Node types, defined in `NodeType` enum."""
	node_name: str
	"""Only applicable for `NodeType.ELEMENT_NODE`"""
	node_value: str
	"""this is where the value from `NodeType.TEXT_NODE` is stored usually"""
	attributes: dict[str, str] | None
	"""slightly changed from the original attributes to be more readable"""
	is_scrollable: bool | None
	"""
	Whether the node is scrollable.
	"""

	## frames
	frame_id: str | None
	content_document: 'EnhancedDOMTreeNode | None'
	"""
	Content document is the document inside a new iframe.
	"""
	## Shadow DOM
	shadow_root_type: ShadowRootType | None
	shadow_roots: list['EnhancedDOMTreeNode'] | None
	"""
	Shadow roots are the shadow DOMs of the element.
	"""

	## Navigation
	parent_node: 'EnhancedDOMTreeNode | None'
	children_nodes: list['EnhancedDOMTreeNode'] | None

	# endregion - DOM Node data

	# region - AX Node data
	ax_node: EnhancedAXNode | None

	# endregion - AX Node data

	# region - Snapshot Node data
	snapshot_node: EnhancedSnapshotNode | None

	# endregion - Snapshot Node data

	def __json__(self) -> dict:
		"""Serializes the node and its descendants to a dictionary, omitting parent references."""
		return {
			'node_id': self.node_id,
			'backend_node_id': self.backend_node_id,
			'node_type': self.node_type.name,
			'node_name': self.node_name,
			'node_value': self.node_value,
			'attributes': self.attributes,
			'is_scrollable': self.is_scrollable,
			'frame_id': self.frame_id,
			'content_document': self.content_document.__json__() if self.content_document else None,
			'shadow_root_type': self.shadow_root_type.value if self.shadow_root_type else None,
			'ax_node': asdict(self.ax_node) if self.ax_node else None,
			'snapshot_node': asdict(self.snapshot_node) if self.snapshot_node else None,
			# these two in the end, so it's easier to read json
			'shadow_roots': [r.__json__() for r in self.shadow_roots] if self.shadow_roots else [],
			'children_nodes': [c.__json__() for c in self.children_nodes] if self.children_nodes else [],
		}


@dataclass
class DOMState:  #
	root: EnhancedDOMTreeNode


class DOMHistoryElement:
	pass
