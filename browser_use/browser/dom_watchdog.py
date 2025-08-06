"""DOM watchdog for browser DOM tree management using CDP."""

import time
from typing import TYPE_CHECKING

from browser_use.browser.events import BrowserErrorEvent, BuildDOMTreeEvent
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.dom.service import DomService
from browser_use.dom.views import (
	EnhancedDOMTreeNode,
	SerializedDOMState,
)
from browser_use.utils import logger

if TYPE_CHECKING:
	pass


class DOMWatchdog(BaseWatchdog):
	"""Handles DOM tree building, serialization, and element access via CDP.

	This watchdog acts as a bridge between the event-driven browser session
	and the DomService implementation, maintaining cached state and providing
	helper methods for other watchdogs.
	"""

	LISTENS_TO = [BuildDOMTreeEvent]
	EMITS = [BrowserErrorEvent]

	# Public properties for other watchdogs
	selector_map: dict[int, EnhancedDOMTreeNode] | None = None
	uuid_selector_map: dict[str, EnhancedDOMTreeNode] | None = None
	current_dom_state: SerializedDOMState | None = None
	enhanced_dom_tree: EnhancedDOMTreeNode | None = None

	# Internal DOM service
	_dom_service: DomService | None = None

	async def attach_to_session(self) -> None:
		"""Attach watchdog to browser session."""
		await super().attach_to_session()
		# DomService will be created on first use

	async def on_BuildDOMTreeEvent(self, event: BuildDOMTreeEvent) -> SerializedDOMState:
		"""Build and serialize DOM tree, returning ready-to-use LLM format.

		Updates public properties:
		- self.selector_map: Index to node mapping for element access
		- self.uuid_selector_map: UUID to node mapping for element access
		- self.current_dom_state: Cached serialized state
		- self.enhanced_dom_tree: Full enhanced DOM tree

		Returns:
			SerializedDOMState with serialized DOM and selector map
		"""
		try:
			page = await self.browser_session.get_current_page()

			# Create or reuse DOM service
			if self._dom_service is None:
				self._dom_service = DomService(browser=self.browser_session, page=page, logger=logger)

			# Get serialized DOM tree using the service
			start = time.time()
			self.current_dom_state, timing_info = await self._dom_service.get_serialized_dom_tree(
				previous_cached_state=event.previous_state
			)
			end = time.time()

			logger.debug(f'Time taken to serialize dom tree: {end - start} seconds')

			# Update selector map for other watchdogs
			self.selector_map = self.current_dom_state.selector_map

			# Build UUID selector map
			self.uuid_selector_map = {}
			if self.selector_map:
				for node in self.selector_map.values():
					if hasattr(node, 'uuid'):
						self.uuid_selector_map[node.uuid] = node

			# Store the enhanced DOM tree if available
			# Note: The service doesn't expose the raw enhanced tree,
			# but we can access it through the selector map if needed
			if self.selector_map:
				# The root node is typically at index 0 or we can traverse up from any node
				for node in self.selector_map.values():
					# Find root by traversing up
					root = node
					while root.parent_node:
						root = root.parent_node
					self.enhanced_dom_tree = root
					break

			return self.current_dom_state

		except Exception as e:
			logger.error(f'Failed to build DOM tree: {e}')
			self.event_bus.dispatch(
				BrowserErrorEvent(
					error_type='DOMBuildFailed',
					message=str(e),
				)
			)
			raise

	# ========== Public Helper Methods ==========

	async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
		"""Get DOM element by index from cached selector map.

		Builds DOM if not cached.

		Returns:
			EnhancedDOMTreeNode or None if index not found
		"""
		if not self.selector_map:
			# Build DOM if not cached
			result = await self.event_bus.dispatch(BuildDOMTreeEvent())
			await result

		return self.selector_map.get(index) if self.selector_map else None

	async def get_element_by_uuid(self, uuid: str) -> EnhancedDOMTreeNode | None:
		"""Get DOM element by UUID from cached selector map.

		Builds DOM if not cached.

		Returns:
			EnhancedDOMTreeNode or None if UUID not found
		"""
		if not self.uuid_selector_map:
			# Build DOM if not cached
			result = await self.event_bus.dispatch(BuildDOMTreeEvent())
			await result

		return self.uuid_selector_map.get(uuid) if self.uuid_selector_map else None

	def clear_cache(self) -> None:
		"""Clear cached DOM state to force rebuild on next access."""
		self.selector_map = None
		self.uuid_selector_map = None
		self.current_dom_state = None
		self.enhanced_dom_tree = None
		# Keep the DOM service instance to reuse its CDP client connection

	def is_file_input(self, element: EnhancedDOMTreeNode) -> bool:
		"""Check if element is a file input."""
		return element.node_name.upper() == 'INPUT' and element.attributes.get('type', '').lower() == 'file'

	@staticmethod
	def is_element_visible_according_to_all_parents(node: EnhancedDOMTreeNode, html_frames: list[EnhancedDOMTreeNode]) -> bool:
		"""Check if the element is visible according to all its parent HTML frames.

		Delegates to the DomService static method.
		"""
		return DomService.is_element_visible_according_to_all_parents(node, html_frames)

	async def __aexit__(self, exc_type, exc_value, traceback):
		"""Clean up DOM service on exit."""
		if self._dom_service:
			await self._dom_service.__aexit__(exc_type, exc_value, traceback)
			self._dom_service = None

	def __del__(self):
		"""Clean up DOM service on deletion."""
		super().__del__()
		# DOM service will clean up its own CDP client
		self._dom_service = None
