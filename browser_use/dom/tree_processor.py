import hashlib
from dataclasses import dataclass
from typing import Optional

from browser_use.dom.views import DOMElementNode


@dataclass
class HashedDomElement:
	"""
	Hash of the dom element to be used as a unique identifier
	"""

	branch_path_hash: str
	attributes_hash: str
	# text_hash: str


class DomTreeProcessor:
	""" "
	Operations on the DOM elements

	@dev be careful - text nodes can change even if elements stay the same
	"""

	@staticmethod
	def find_element_in_tree(
		dom_element: DOMElementNode, tree: DOMElementNode
	) -> Optional[DOMElementNode]:
		hashed_dom_element = DomTreeProcessor._hash_dom_element(dom_element)

		def process_node(node: DOMElementNode):
			if node.highlight_index is not None:
				hashed_node = DomTreeProcessor._hash_dom_element(node)
				if hashed_node == hashed_dom_element:
					return node
			for child in node.children:
				if isinstance(child, DOMElementNode):
					result = process_node(child)
					if result is not None:
						return result
			return None

		return process_node(tree)

	@staticmethod
	def compare_dom_elements(dom_element_1: DOMElementNode, dom_element_2: DOMElementNode) -> bool:
		hashed_dom_element_1 = DomTreeProcessor._hash_dom_element(dom_element_1)
		hashed_dom_element_2 = DomTreeProcessor._hash_dom_element(dom_element_2)

		return hashed_dom_element_1 == hashed_dom_element_2

	@staticmethod
	def _hash_dom_element(dom_element: DOMElementNode) -> HashedDomElement:
		branch_path_hash = DomTreeProcessor._parent_branch_path_hash(dom_element)
		attributes_hash = DomTreeProcessor._attributes_hash(dom_element)
		# text_hash = DomTreeProcessor._text_hash(dom_element)

		return HashedDomElement(branch_path_hash, attributes_hash)

	@staticmethod
	def _parent_branch_path_hash(dom_element: DOMElementNode) -> str:
		parents: list[DOMElementNode] = []
		current_element: DOMElementNode = dom_element
		while current_element.parent is not None:
			parents.append(current_element)
			current_element = current_element.parent

		parents.reverse()

		parent_branch_path = '/'.join(parent.tag_name for parent in parents)
		return hashlib.sha256(parent_branch_path.encode()).hexdigest()

	@staticmethod
	def _attributes_hash(dom_element: DOMElementNode) -> str:
		attributes_string = ''.join(
			f'{key}={value}' for key, value in dom_element.attributes.items()
		)
		return hashlib.sha256(attributes_string.encode()).hexdigest()

	@staticmethod
	def _text_hash(dom_element: DOMElementNode) -> str:
		""" """
		text_string = dom_element.get_all_text_till_next_clickable_element()
		return hashlib.sha256(text_string.encode()).hexdigest()
