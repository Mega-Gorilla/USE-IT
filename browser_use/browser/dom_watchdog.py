"""DOM watchdog for browser DOM tree management using CDP."""

import asyncio
import time
from typing import TYPE_CHECKING

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStateRequestEvent,
	BuildDOMTreeEvent,
	ScreenshotEvent,
)
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.dom.service import DomService
from browser_use.dom.views import (
	EnhancedDOMTreeNode,
	SerializedDOMState,
)
from browser_use.utils import is_new_tab_page, logger

if TYPE_CHECKING:
	from browser_use.browser.views import BrowserStateSummary, PageInfo, TabInfo


class DOMWatchdog(BaseWatchdog):
	"""Handles DOM tree building, serialization, and element access via CDP.

	This watchdog acts as a bridge between the event-driven browser session
	and the DomService implementation, maintaining cached state and providing
	helper methods for other watchdogs.
	"""

	LISTENS_TO = [BuildDOMTreeEvent, BrowserStateRequestEvent]
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

	async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> 'BrowserStateSummary':
		"""Handle browser state request by coordinating DOM building and screenshot capture.

		This is the main entry point for getting the complete browser state.

		Args:
			event: The browser state request event with options

		Returns:
			Complete BrowserStateSummary with DOM, screenshot, and target info
		"""
		from browser_use.browser.views import BrowserStateSummary, PageInfo

		page_url = await self.browser_session.get_current_page_url()

		# Check if this is a new tab or chrome:// target early for optimization
		is_empty_page = is_new_tab_page(page_url) or page_url.startswith('chrome://')

		try:
			# Fast path for empty pages
			if is_empty_page:
				logger.debug(f'⚡ Fast path for empty target: {page_url}')

				# Create minimal DOM state
				content = SerializedDOMState(_root=None, selector_map={})

				# Get tabs info
				tabs_info = await self.browser_session.get_tabs_info()

				# Skip screenshot for empty pages
				screenshot_b64 = None

				# Use default viewport dimensions
				viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
				page_info = PageInfo(
					viewport_width=viewport['width'],
					viewport_height=viewport['height'],
					page_width=viewport['width'],
					page_height=viewport['height'],
					scroll_x=0,
					scroll_y=0,
					pixels_above=0,
					pixels_below=0,
					pixels_left=0,
					pixels_right=0,
				)

				return BrowserStateSummary(
					dom_state=content,
					url=page_url,
					title='New Tab' if is_new_tab_page(page_url) else 'Chrome Page',
					tabs=tabs_info,
					screenshot=screenshot_b64,
					page_info=page_info,
					pixels_above=0,
					pixels_below=0,
					browser_errors=[],
					is_pdf_viewer=False,
				)

			# Normal path: Build DOM tree if requested
			if event.include_dom:
				logger.debug('🌳 Building DOM tree...')

				# Dispatch BuildDOMTreeEvent and wait for result
				# The DOM watchdog will handle this and update our cached selector map
				dom_event = self.event_bus.dispatch(
					BuildDOMTreeEvent(
						previous_state=self.browser_session._cached_browser_state_summary.dom_state
						if self.browser_session._cached_browser_state_summary
						else None
					)
				)
				content = await dom_event.event_result()

				if not content:
					# Fallback to minimal DOM state
					logger.warning('DOM build returned no content, using minimal state')
					content = SerializedDOMState(_root=None, selector_map={})
			else:
				# Skip DOM building if not requested
				content = SerializedDOMState(_root=None, selector_map={})

			# Get screenshot if requested
			screenshot_b64 = None
			if event.include_screenshot:
				try:
					screenshot_event = self.event_bus.dispatch(ScreenshotEvent(full_page=False))
					screenshot_result = await screenshot_event.event_result()
					if screenshot_result:
						screenshot_b64 = screenshot_result.get('screenshot')
				except Exception as e:
					logger.warning(f'Screenshot failed: {e}')

			# Get target info and tabs
			tabs_info = await self.browser_session.get_tabs_info()

			# Get target title safely
			try:
				title = await asyncio.wait_for(self.browser_session.get_current_page_title(), timeout=2.0)
			except Exception:
				title = 'Page'

			# TODO: Get proper target info from CDP
			viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
			page_info = PageInfo(
				viewport_width=viewport['width'],
				viewport_height=viewport['height'],
				page_width=viewport['width'],
				page_height=viewport['height'],
				scroll_x=0,
				scroll_y=0,
				pixels_above=0,
				pixels_below=0,
				pixels_left=0,
				pixels_right=0,
			)

			# Check for PDF viewer
			is_pdf_viewer = page_url.endswith('.pdf') or '/pdf/' in page_url

			# Build and cache the browser state summary
			browser_state = BrowserStateSummary(
				dom_state=content,
				url=page_url,
				title=title,
				tabs=tabs_info,
				screenshot=screenshot_b64,
				page_info=page_info,
				pixels_above=0,
				pixels_below=0,
				browser_errors=[],
				is_pdf_viewer=is_pdf_viewer,
			)

			# Cache the state
			self.browser_session._cached_browser_state_summary = browser_state

			return browser_state

		except Exception as e:
			logger.error(f'Failed to get browser state: {e}')

			# Return minimal recovery state
			return BrowserStateSummary(
				dom_state=SerializedDOMState(_root=None, selector_map={}),
				url=page_url if 'page_url' in locals() else '',
				title='Error',
				tabs=[],
				screenshot=None,
				page_info=PageInfo(
					viewport_width=1280,
					viewport_height=720,
					page_width=1280,
					page_height=720,
					scroll_x=0,
					scroll_y=0,
					pixels_above=0,
					pixels_below=0,
					pixels_left=0,
					pixels_right=0,
				),
				pixels_above=0,
				pixels_below=0,
				browser_errors=[str(e)],
				is_pdf_viewer=False,
			)

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
			# Remove any existing highlights before building new DOM
			try:
				await self.browser_session.remove_highlights()
			except Exception as e:
				logger.debug(f'Failed to remove existing highlights: {e}')
			
			# Create or reuse DOM service
			if self._dom_service is None:
				self._dom_service = DomService(browser_session=self.browser_session, logger=logger)

			# Get serialized DOM tree using the service
			start = time.time()
			print(f"DOMWatchdog: About to call get_serialized_dom_tree with dom_service={self._dom_service}")
			self.current_dom_state, self.enhanced_dom_tree, timing_info = await self._dom_service.get_serialized_dom_tree(
				previous_cached_state=event.previous_state
			)
			print(f"DOMWatchdog: Returned from get_serialized_dom_tree")
			end = time.time()

			logger.debug(f'Time taken to get DOM tree: {end - start} seconds')
			logger.debug(f'Timing breakdown: {timing_info}')

			# Update selector map for other watchdogs
			self.selector_map = self.current_dom_state.selector_map
			
			# Inject highlighting for visual feedback if we have elements
			if self.selector_map and self._dom_service:
				try:
					from browser_use.dom.debug.highlights import inject_highlighting_script
					await inject_highlighting_script(self._dom_service, self.selector_map)
					logger.debug(f'Injected highlighting for {len(self.selector_map)} elements')
				except Exception as e:
					logger.debug(f'Failed to inject highlighting: {e}')

			# Update BrowserSession's cached selector map
			if self.browser_session:
				self.browser_session.update_cached_selector_map(self.selector_map)

			# Build UUID selector map
			self.uuid_selector_map = {}
			if self.selector_map:
				for node in self.selector_map.values():
					if hasattr(node, 'uuid'):
						self.uuid_selector_map[node.uuid] = node

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
