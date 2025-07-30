"""Remote browser session that connects via CDP."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Self

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


if TYPE_CHECKING:
	from browser_use.browser.profile import BrowserProfile


class RemoteBrowserConnection(BaseModel):
	"""Base connection for all browser connections via CDP.
	
	This class handles connections to existing browsers via Chrome DevTools Protocol (CDP).
	"""
	
	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)
	
	# Configuration
	browser_profile: BrowserProfile
	
	# Connection URL
	cdp_url: str
	
	# Runtime state (private)
	_playwright: Playwright | None = PrivateAttr(default=None)
	_browser: Browser | None = PrivateAttr(default=None)
	_context: BrowserContext | None = PrivateAttr(default=None)
	_current_page: Page | None = PrivateAttr(default=None)
	_pages: list[Page] = PrivateAttr(default_factory=list)
	
	# State tracking
	_started: bool = PrivateAttr(default=False)
	
	async def start(self) -> Self:
		"""Connect to the remote browser and set up the session."""
		if self._started:
			return self
		
		self._playwright = await async_playwright().start()
		
		try:
			# Connect via CDP
			await self._connect_via_cdp()
			
			# Set up the browser context
			await self._setup_browser_context()
			
			self._started = True
			return self
			
		except Exception:
			# Clean up on failure
			await self._cleanup_playwright()
			raise
	
	async def _connect_via_cdp(self) -> None:
		"""Connect to browser via Chrome DevTools Protocol."""
		# All channels (chrome, chromium, msedge, etc.) use chromium in playwright
		browser_type = self._playwright.chromium
		
		# CDP connection args from profile
		connection_args = {
			'endpoint_url': self.cdp_url,
			**self.browser_profile.kwargs_for_cdp_connection(),
		}
		
		self._browser = await browser_type.connect_over_cdp(**connection_args)
	
	async def _setup_browser_context(self) -> None:
		"""Set up the browser context with profile settings."""
		if not self._browser:
			raise RuntimeError("Browser not connected")
		
		# Get or create context
		contexts = self._browser.contexts
		if contexts:
			self._context = contexts[0]
		else:
			# Create new context with profile settings
			context_args = self.browser_profile.kwargs_for_browser_context()
			self._context = await self._browser.new_context(**context_args)
		
		# Track existing pages
		self._pages = list(self._context.pages)
		if self._pages:
			self._current_page = self._pages[0]
	
	async def stop(self) -> None:
		"""Disconnect from the browser and clean up resources."""
		if not self._started:
			return
		
		# Close context if we created it
		if self._context and not self._context.pages:
			await self._context.close()
		
		# CDP connections should be disconnected, not closed
		# The browser instance will be cleaned up when playwright stops
		
		# Clean up playwright
		await self._cleanup_playwright()
		
		# Reset state
		self._browser = None
		self._context = None
		self._current_page = None
		self._pages.clear()
		self._started = False
	
	async def _cleanup_playwright(self) -> None:
		"""Clean up playwright instance."""
		if self._playwright:
			await self._playwright.stop()
			self._playwright = None
	
	# Page management methods
	async def new_page(self, url: str | None = None) -> Page:
		"""Create a new page in the browser context."""
		if not self._context:
			raise RuntimeError("Browser session not started")
		
		page = await self._context.new_page()
		self._pages.append(page)
		self._current_page = page
		
		if url:
			await page.goto(url)
		
		return page
	
	async def get_current_page(self) -> Page:
		"""Get the current active page, creating one if needed."""
		if not self._context:
			raise RuntimeError("Browser session not started")
		
		if not self._current_page or self._current_page.is_closed():
			# Find first non-closed page
			for page in self._pages:
				if not page.is_closed():
					self._current_page = page
					return page
			
			# No open pages, create new one
			return await self.new_page()
		
		return self._current_page
	
	@property
	def browser(self) -> Browser | None:
		"""Get the connected browser instance."""
		return self._browser
	
	@property
	def context(self) -> BrowserContext | None:
		"""Get the browser context."""
		return self._context
	
	@property
	def is_connected(self) -> bool:
		"""Check if connected to a browser."""
		return self._browser is not None and self._browser.is_connected()