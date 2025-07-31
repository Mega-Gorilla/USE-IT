"""Downloads watchdog for monitoring and handling file downloads."""

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from weakref import WeakSet

import anyio
from bubus import EventBus
from playwright.async_api import Download, Page
from pydantic import BaseModel, ConfigDict, PrivateAttr

from browser_use.browser.events import (
	FileDownloadedEvent,
	NavigationCompleteEvent,
	TabClosedEvent,
	TabCreatedEvent,
)
from browser_use.utils import logger

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession


class DownloadsWatchdog(BaseModel):
	"""Monitors downloads and handles file download events."""

	model_config = ConfigDict(
		arbitrary_types_allowed=True,
		validate_assignment=True,
		extra='forbid',
	)

	event_bus: EventBus
	browser_session: 'BrowserSession'  # Dependency injection instead of private attrs

	# Private state
	_pages: WeakSet[Page] = PrivateAttr(default_factory=WeakSet)
	_active_downloads: dict[str, Download] = PrivateAttr(default_factory=dict)
	_pdf_viewer_cache: dict[str, bool] = PrivateAttr(default_factory=dict)  # Cache PDF viewer status by page URL

	def __init__(self, event_bus: EventBus, browser_session: 'BrowserSession', **kwargs):
		"""Initialize watchdog with event bus and browser session."""
		super().__init__(event_bus=event_bus, browser_session=browser_session, **kwargs)
		self._register_handlers()

	def _register_handlers(self) -> None:
		"""Register event handlers."""
		self.event_bus.on(TabCreatedEvent, self._handle_tab_created)
		self.event_bus.on(TabClosedEvent, self._handle_tab_closed)
		self.event_bus.on(NavigationCompleteEvent, self._handle_navigation_complete)

	async def _handle_tab_created(self, event: TabCreatedEvent) -> None:
		"""Monitor new tabs for downloads."""
		# Tab will be added via add_page method from session
		pass

	async def _handle_tab_closed(self, event: TabClosedEvent) -> None:
		"""Stop monitoring closed tabs."""
		# Tab will be removed automatically via WeakSet
		pass

	async def _handle_navigation_complete(self, event: NavigationCompleteEvent) -> None:
		"""Check for PDFs after navigation completes."""
		# Clear PDF cache for the navigated URL since content may have changed
		if event.url in self._pdf_viewer_cache:
			del self._pdf_viewer_cache[event.url]

		# Check if auto-download is enabled
		if not self._is_auto_download_enabled():
			return

		# Get the page that navigated
		try:
			if not hasattr(self.browser_session, '_browser_context') or not self.browser_session._browser_context:
				return

			pages = self.browser_session._browser_context.pages
			if 0 <= event.tab_index < len(pages):
				page = pages[event.tab_index]

				# Check if it's a PDF and auto-download if needed
				if await self.check_for_pdf_viewer(page):
					logger.info(f'[DownloadsWatchdog] PDF detected after navigation to {event.url}')
					await self.trigger_pdf_download(page)
		except Exception as e:
			logger.error(f'[DownloadsWatchdog] Error checking for PDF after navigation: {e}')

	def _is_auto_download_enabled(self) -> bool:
		"""Check if PDF auto-download is enabled."""
		return getattr(self.browser_session, '_auto_download_pdfs', True)

	def add_page(self, page: Page) -> None:
		"""Add a page to monitor for downloads."""
		self._pages.add(page)
		self._setup_page_listeners(page)
		logger.debug(f'[DownloadsWatchdog] Added page to monitoring: {page.url}')

	def _setup_page_listeners(self, page: Page) -> None:
		"""Set up download listeners for a page."""
		# Monitor download events
		logger.info(f'[DownloadsWatchdog] Setting up download listener for page: {page.url}')
		page.on('download', lambda download: asyncio.create_task(self._handle_download(download)))

	async def _handle_download(self, download: Download) -> None:
		"""Handle a download event."""
		download_id = f'{id(download)}'
		self._active_downloads[download_id] = download
		logger.info(f'[DownloadsWatchdog] Handling download: {download.suggested_filename} from {download.url[:100]}...')

		try:
			# Get download info
			url = download.url
			suggested_filename = download.suggested_filename

			# Determine download directory from browser profile
			downloads_dir = self.browser_session.browser_profile.downloads_path
			if not downloads_dir:
				downloads_dir = str(Path.home() / 'Downloads')
			else:
				downloads_dir = str(downloads_dir)  # Ensure it's a string

			# Ensure unique filename
			unique_filename = await self._get_unique_filename(downloads_dir, suggested_filename)
			download_path = Path(downloads_dir) / unique_filename

			logger.info(f'[DownloadsWatchdog] Download started: {unique_filename} from {url[:100]}...')

			# Save the download
			await download.save_as(str(download_path))

			# Get file info
			file_size = download_path.stat().st_size if download_path.exists() else 0

			# Determine file type from extension
			file_ext = download_path.suffix.lower().lstrip('.')
			file_type = file_ext if file_ext else None

			# Try to get MIME type from response headers if available
			mime_type = None
			# Note: Playwright doesn't expose response headers directly from Download object

			# Check if this was a PDF auto-download
			auto_download = False
			if file_type == 'pdf':
				auto_download = self._is_auto_download_enabled()

			# Emit download event
			self.event_bus.dispatch(
				FileDownloadedEvent(
					url=url,
					path=str(download_path),
					file_name=suggested_filename,
					file_size=file_size,
					file_type=file_type,
					mime_type=mime_type,
					from_cache=False,
					auto_download=auto_download,
				)
			)

			logger.info(
				f'[DownloadsWatchdog] Download completed: {suggested_filename} ({file_size} bytes) saved to {download_path}'
			)

			# File is now tracked on filesystem, no need to track in memory

		except Exception as e:
			logger.error(f'[DownloadsWatchdog] Error handling download: {e}')
		finally:
			# Clean up tracking
			if download_id in self._active_downloads:
				del self._active_downloads[download_id]

	async def check_for_pdf_viewer(self, page: Page) -> bool:
		"""Check if the current page is Chrome's built-in PDF viewer.

		Returns True if a PDF is detected and should be downloaded.
		"""
		# Check cache first
		page_url = page.url
		if page_url in self._pdf_viewer_cache:
			return self._pdf_viewer_cache[page_url]

		try:
			# Check if we're in Chrome's PDF viewer
			is_pdf_viewer = await page.evaluate("""
				() => {
					// Check for Chrome's built-in PDF viewer (both old and new selectors)
					const pdfEmbed = document.querySelector('embed[type="application/x-google-chrome-pdf"]') ||
									 document.querySelector('embed[type="application/pdf"]');
					if (pdfEmbed && pdfEmbed.src) {
						return {
							isPdf: true,
							url: pdfEmbed.src,
							isChromePdfViewer: true
						};
					}
					
					// Check for direct PDF navigation
					if (document.contentType === 'application/pdf') {
						return {
							isPdf: true,
							url: window.location.href,
							isDirectPdf: true
						};
					}
					
					// Also check if the URL ends with .pdf or has PDF in it
					const url = window.location.href;
					const isPdfUrl = url.toLowerCase().includes('.pdf');
					if (isPdfUrl) {
						return {
							isPdf: true,
							url: url,
							isPdfUrl: true
						};
					}
					
					// Check for PDF in iframe
					const iframes = document.querySelectorAll('iframe');
					for (const iframe of iframes) {
						try {
							const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
							if (iframeDoc.contentType === 'application/pdf') {
								return {
									isPdf: true,
									url: iframe.src,
									isIframePdf: true
								};
							}
						} catch (e) {
							// Cross-origin iframe, skip
						}
					}
					
					return { isPdf: false };
				}
			""")

			if is_pdf_viewer.get('isPdf', False):
				logger.info(
					f'[DownloadsWatchdog] PDF detected: {is_pdf_viewer.get("url", "unknown")} '
					f'(type: {"Chrome viewer" if is_pdf_viewer.get("isChromePdfViewer") else "direct PDF"})'
				)
				self._pdf_viewer_cache[page_url] = True
				return True

			self._pdf_viewer_cache[page_url] = False
			return False

		except Exception as e:
			logger.debug(f'[DownloadsWatchdog] Error checking for PDF viewer: {e}')
			return False

	async def trigger_pdf_download(self, page: Page) -> str | None:
		"""Trigger download of a PDF from Chrome's PDF viewer.

		Returns the download path if successful, None otherwise.
		"""
		if not self.browser_session.browser_profile.downloads_path:
			logger.warning('[DownloadsWatchdog] No downloads path configured')
			return None

		try:
			# Try to get the PDF URL
			pdf_info = await page.evaluate("""
				() => {
					const embedElement = document.querySelector('embed[type="application/x-google-chrome-pdf"]') ||
									   document.querySelector('embed[type="application/pdf"]');
					if (embedElement && embedElement.src) {
						return { url: embedElement.src };
					}
					return { url: window.location.href };
				}
			""")

			pdf_url = pdf_info.get('url', '')
			if not pdf_url:
				logger.warning('[DownloadsWatchdog] Could not determine PDF URL for download')
				return None

			# Generate filename from URL
			pdf_filename = os.path.basename(pdf_url.split('?')[0])  # Remove query params
			if not pdf_filename or not pdf_filename.endswith('.pdf'):
				parsed = urlparse(pdf_url)
				pdf_filename = os.path.basename(parsed.path) or 'document.pdf'
				if not pdf_filename.endswith('.pdf'):
					pdf_filename += '.pdf'

			# Check if already downloaded by looking in the downloads directory
			downloads_dir = str(self.browser_session.browser_profile.downloads_path)
			if os.path.exists(downloads_dir):
				existing_files = os.listdir(downloads_dir)
				if pdf_filename in existing_files:
					logger.debug(f'[DownloadsWatchdog] PDF already downloaded: {pdf_filename}')
					return None

			logger.info(f'[DownloadsWatchdog] Auto-downloading PDF from: {pdf_url[:100]}...')

			# Download using JavaScript fetch to leverage browser cache
			try:
				# Properly escape the URL to prevent JavaScript injection
				escaped_pdf_url = json.dumps(pdf_url)

				download_result = await page.evaluate(f"""
					async () => {{
						try {{
							// Use fetch with cache: 'force-cache' to prioritize cached version
							const response = await fetch({escaped_pdf_url}, {{
								cache: 'force-cache'
							}});
							if (!response.ok) {{
								throw new Error(`HTTP error! status: ${{response.status}}`);
							}}
							const blob = await response.blob();
							const arrayBuffer = await blob.arrayBuffer();
							const uint8Array = new Uint8Array(arrayBuffer);
							
							// Check if served from cache
							const fromCache = response.headers.has('age') || 
											 !response.headers.has('date') ||
											 performance.getEntriesByName({escaped_pdf_url}).some(entry => 
												 entry.transferSize === 0 || entry.transferSize < entry.encodedBodySize
											 );
											 
							return {{ 
								data: Array.from(uint8Array),
								fromCache: fromCache,
								responseSize: uint8Array.length,
								transferSize: response.headers.get('content-length') || 'unknown'
							}};
						}} catch (error) {{
							throw new Error(`Fetch failed: ${{error.message}}`);
						}}
					}}
				""")

				if download_result and download_result.get('data') and len(download_result['data']) > 0:
					# Ensure unique filename
					downloads_dir = str(self.browser_session.browser_profile.downloads_path)
					unique_filename = await self._get_unique_filename(downloads_dir, pdf_filename)
					download_path = os.path.join(downloads_dir, unique_filename)

					# Save the PDF asynchronously
					async with await anyio.open_file(download_path, 'wb') as f:
						await f.write(bytes(download_result['data']))

					# File is now tracked on filesystem, no need to track in memory

					# Log cache information
					cache_status = 'from cache' if download_result.get('fromCache') else 'from network'
					response_size = download_result.get('responseSize', 0)
					logger.info(
						f'[DownloadsWatchdog] Auto-downloaded PDF ({cache_status}, {response_size:,} bytes): {download_path}'
					)

					# Emit file downloaded event
					self.event_bus.dispatch(
						FileDownloadedEvent(
							url=pdf_url,
							path=download_path,
							file_name=unique_filename,
							file_size=response_size,
							file_type='pdf',
							mime_type='application/pdf',
							from_cache=download_result.get('fromCache', False),
							auto_download=True,
						)
					)

					return download_path
				else:
					logger.warning(f'[DownloadsWatchdog] No data received when downloading PDF from {pdf_url}')
					return None

			except Exception as e:
				logger.warning(f'[DownloadsWatchdog] Failed to auto-download PDF from {pdf_url}: {type(e).__name__}: {e}')
				return None

		except Exception as e:
			logger.error(f'[DownloadsWatchdog] Error in PDF download: {type(e).__name__}: {e}')
			return None

	@staticmethod
	async def _get_unique_filename(directory: str, filename: str) -> str:
		"""Generate a unique filename for downloads by appending (1), (2), etc., if a file already exists."""
		base, ext = os.path.splitext(filename)
		counter = 1
		new_filename = filename
		while os.path.exists(os.path.join(directory, new_filename)):
			new_filename = f'{base} ({counter}){ext}'
			counter += 1
		return new_filename
