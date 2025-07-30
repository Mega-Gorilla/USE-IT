"""Browser session interface for agent and controller."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.local import LocalBrowserConnection
from browser_use.browser.remote import RemoteBrowserConnection

if TYPE_CHECKING:
	from playwright.async_api import Browser, BrowserContext, Page
	from browser_use.browser.profile import BrowserProfile


class BrowserSession(BaseModel):
	"""Public interface for browser sessions used by agent and controller.
	
	This class wraps either LocalBrowserConnection or RemoteBrowserConnection
	and exposes only the methods needed by the agent and controller.
	"""
	
	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)
	
	# Core configuration
	browser_profile: BrowserProfile
	id: str = Field(default_factory=lambda: uuid7str())
	
	# Connection info (for backwards compatibility)
	cdp_url: str | None = None
	
	# Private implementation
	_impl: LocalBrowserConnection | RemoteBrowserConnection = PrivateAttr()
	_owns_browser_resources: bool = PrivateAttr(default=True)
	
	def __init__(
		self,
		browser_profile: BrowserProfile,
		cdp_url: str | None = None,
		wss_url: str | None = None,
		browser_pid: int | None = None,
		**kwargs: Any,
	):
		"""Initialize a browser session.
		
		Args:
			browser_profile: Browser configuration profile
			cdp_url: CDP URL for connecting to existing browser
			wss_url: WSS URL for connecting to remote playwright (DEPRECATED)
			browser_pid: Process ID of existing browser (DEPRECATED)
			**kwargs: Additional arguments
		"""
		# Handle deprecated parameters
		if wss_url:
			raise ValueError(
				"wss_url is no longer supported. Browser Use now only supports CDP connections. "
				"Please use cdp_url instead."
			)
		
		# Initialize base model
		super().__init__(
			browser_profile=browser_profile,
			cdp_url=cdp_url,
			**kwargs,
		)
		
		if browser_pid is not None:
			warnings.warn(
				"Passing browser_pid to BrowserSession is deprecated. "
				"Use LocalBrowserConnection.from_existing_pid(browser_profile, pid, cdp_url) instead.",
				DeprecationWarning,
				stacklevel=2,
			)
			if not cdp_url:
				raise ValueError("cdp_url is required when browser_pid is provided")
			
			# Create LocalBrowserConnection from existing PID
			self._impl = LocalBrowserConnection.from_existing_pid(
				browser_profile=browser_profile,
				pid=browser_pid,
				cdp_url=cdp_url,
				**kwargs,
			)
		elif cdp_url:
			# Remote browser connection
			self._impl = RemoteBrowserConnection(
				browser_profile=browser_profile,
				cdp_url=cdp_url,
				**kwargs,
			)
			self._owns_browser_resources = False
		else:
			# Local browser launch
			self._impl = LocalBrowserConnection(
				browser_profile=browser_profile,
				**kwargs,
			)
			self._owns_browser_resources = True
		
		# Update cdp_url if not set
		if not self.cdp_url and hasattr(self._impl, 'cdp_url'):
			self.cdp_url = self._impl.cdp_url
	
	async def start(self) -> Self:
		"""Start the browser session."""
		await self._impl.start()
		# Update cdp_url after start
		if hasattr(self._impl, 'cdp_url'):
			self.cdp_url = self._impl.cdp_url
		return self
	
	async def stop(self) -> None:
		"""Stop the browser session."""
		await self._impl.stop()
	
	async def __aenter__(self) -> Self:
		"""Async context manager entry."""
		return await self.start()
	
	async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
		"""Async context manager exit."""
		await self.stop()
	
	# Browser properties
	@property
	def browser(self) -> Browser | None:
		"""Get the browser instance."""
		return self._impl.browser
	
	@property
	def browser_context(self) -> BrowserContext | None:
		"""Get the browser context."""
		return self._impl.context
	
	# Page management
	async def get_current_page(self) -> Page:
		"""Get the current active page."""
		return await self._impl.get_current_page()
	
	async def new_page(self, url: str | None = None) -> Page:
		"""Create a new page."""
		return await self._impl.new_page(url)
	
	# Model copy support
	def model_copy(self, **kwargs) -> Self:
		"""Create a copy of this session."""
		# Create a new instance with the same impl
		copy = self.__class__(
			browser_profile=self.browser_profile,
			cdp_url=self.cdp_url,
			**kwargs,
		)
		copy._impl = self._impl
		copy._owns_browser_resources = self._owns_browser_resources
		return copy
	
	# These methods need to be implemented by looking at the old BrowserSession
	# to understand their signatures and delegate to the appropriate impl methods
	@property
	def agent_current_page(self) -> Page | None:
		"""Get the agent's current page."""
		return getattr(self._impl, '_current_page', None)
	
	@property
	def downloaded_files(self) -> list[str]:
		"""Get list of downloaded files."""
		# This needs to be implemented based on the old BrowserSession
		return []
	
	@property
	def tabs(self) -> list[Page]:
		"""Get all open tabs/pages."""
		return getattr(self._impl, '_pages', [])
	
	async def create_new_tab(self, url: str | None = None) -> Page:
		"""Create a new tab."""
		return await self.new_page(url)
	
	async def switch_to_tab(self, tab_index: int) -> None:
		"""Switch to a tab by index."""
		pages = self.tabs
		if 0 <= tab_index < len(pages):
			self._impl._current_page = pages[tab_index]
		elif tab_index == -1 and pages:
			self._impl._current_page = pages[-1]
	
	async def navigate_to(self, url: str) -> None:
		"""Navigate the current page to a URL."""
		page = await self.get_current_page()
		await page.goto(url)
	
	async def go_back(self) -> None:
		"""Go back in the browser history."""
		page = await self.get_current_page()
		await page.go_back()
	
	# DOM element methods - these need to be implemented based on old BrowserSession
	async def get_browser_state_with_recovery(self, **kwargs) -> Any:
		"""Get browser state with recovery."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("get_browser_state_with_recovery needs to be implemented")
	
	async def get_selector_map(self) -> dict:
		"""Get selector map."""
		# Placeholder - needs implementation from old BrowserSession
		return {}
	
	async def get_dom_element_by_index(self, index: int) -> Any:
		"""Get DOM element by index."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("get_dom_element_by_index needs to be implemented")
	
	async def find_file_upload_element_by_index(self, index: int) -> Any:
		"""Find file upload element by index."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("find_file_upload_element_by_index needs to be implemented")
	
	async def get_locate_element(self, dom_element: Any) -> Any:
		"""Get locate element."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("get_locate_element needs to be implemented")
	
	def is_file_input(self, element: Any) -> bool:
		"""Check if element is a file input."""
		# Placeholder - needs implementation from old BrowserSession
		return False
	
	async def _click_element_node(self, element: Any) -> str | None:
		"""Click an element node."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("_click_element_node needs to be implemented")
	
	async def _input_text_element_node(self, element: Any, text: str) -> None:
		"""Input text into an element node."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("_input_text_element_node needs to be implemented")
	
	async def _scroll_container(self, dy: int) -> None:
		"""Scroll container."""
		# Placeholder - needs implementation from old BrowserSession
		raise NotImplementedError("_scroll_container needs to be implemented")


# Import uuid7str for ID generation
try:
	from uuid_extensions import uuid7str
except ImportError:
	import uuid
	def uuid7str() -> str:
		return str(uuid.uuid4())