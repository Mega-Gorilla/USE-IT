from dataclasses import dataclass
from enum import Enum

from cdp_use.cdp.dom.types import ShadowRootType
from pydantic import BaseModel

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


class DOMNodeAttributes(BaseModel):
	name: str
	value: str


@dataclass
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

	_node_type: NodeType
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

	# endregion - AX Node data

	# region - Snapshot Node data

	# endregion - Snapshot Node data


class DOMState:
	pass


class DOMHistoryElement:
	pass
