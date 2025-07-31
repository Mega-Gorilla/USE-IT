"""Downloads watchdog for monitoring and handling file downloads."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from weakref import WeakSet

from bubus import EventBus
from playwright.async_api import Download, Page
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from browser_use.browser.events import (
	FileDownloadedEvent,
	TabCreatedEvent,
	TabClosedEvent,
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
	downloads_dir: Path | None = Field(default=None)

	# Private state
	_pages: WeakSet[Page] = PrivateAttr(default_factory=WeakSet)
	_active_downloads: dict[str, Download] = PrivateAttr(default_factory=dict)

	def __init__(self, event_bus: EventBus, browser_session: 'BrowserSession', **kwargs):
		"""Initialize watchdog with event bus and browser session."""
		super().__init__(event_bus=event_bus, browser_session=browser_session, **kwargs)
		self._register_handlers()

	def _register_handlers(self) -> None:
		"""Register event handlers."""
		self.event_bus.on(TabCreatedEvent, self._handle_tab_created)
		self.event_bus.on(TabClosedEvent, self._handle_tab_closed)

	async def _handle_tab_created(self, event: TabCreatedEvent) -> None:
		"""Monitor new tabs for downloads."""
		# Tab will be added via add_page method from session
		pass

	async def _handle_tab_closed(self, event: TabClosedEvent) -> None:
		"""Stop monitoring closed tabs."""
		# Tab will be removed automatically via WeakSet
		pass

	def add_page(self, page: Page) -> None:
		"""Add a page to monitor for downloads."""
		self._pages.add(page)
		self._setup_page_listeners(page)
		logger.debug(f'[DownloadsWatchdog] Added page to monitoring: {page.url}')

	def _setup_page_listeners(self, page: Page) -> None:
		"""Set up download listeners for a page."""
		# Monitor download events
		page.on('download', lambda download: asyncio.create_task(self._handle_download(download)))

	async def _handle_download(self, download: Download) -> None:
		"""Handle a download event."""
		download_id = f"{id(download)}"
		self._active_downloads[download_id] = download
		
		try:
			# Get download info
			url = download.url
			suggested_filename = download.suggested_filename
			
			# Determine download path
			if self.downloads_dir:
				download_path = self.downloads_dir / suggested_filename
			else:
				# Use default downloads directory
				download_path = Path.home() / "Downloads" / suggested_filename
			
			logger.info(f'[DownloadsWatchdog] Download started: {suggested_filename} from {url[:100]}...')
			
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
			if file_type == 'pdf' and hasattr(self.browser_session, '_auto_download_pdfs'):
				auto_download = self.browser_session._auto_download_pdfs
			
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
				f'[DownloadsWatchdog] Download completed: {suggested_filename} '
				f'({file_size} bytes) saved to {download_path}'
			)
			
			# Track downloaded file in browser session
			if hasattr(self.browser_session, '_downloaded_files'):
				self.browser_session._downloaded_files.append(str(download_path))
			
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
		try:
			# Check if we're in Chrome's PDF viewer
			is_pdf_viewer = await page.evaluate("""
				() => {
					// Check for Chrome PDF viewer
					const embedElement = document.querySelector('embed[type="application/pdf"]');
					if (embedElement && embedElement.src) {
						return {
							isPdf: true,
							url: embedElement.src,
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
				return True
				
			return False
			
		except Exception as e:
			logger.debug(f'[DownloadsWatchdog] Error checking for PDF viewer: {e}')
			return False

	async def trigger_pdf_download(self, page: Page) -> None:
		"""Trigger download of a PDF from Chrome's PDF viewer."""
		try:
			# Try to get the PDF URL
			pdf_info = await page.evaluate("""
				() => {
					const embedElement = document.querySelector('embed[type="application/pdf"]');
					if (embedElement && embedElement.src) {
						return { url: embedElement.src };
					}
					return { url: window.location.href };
				}
			""")
			
			pdf_url = pdf_info.get('url', '')
			if not pdf_url:
				logger.warning('[DownloadsWatchdog] Could not determine PDF URL for download')
				return
			
			logger.info(f'[DownloadsWatchdog] Triggering PDF download from: {pdf_url[:100]}...')
			
			# Navigate to the PDF URL with a download-triggering approach
			# This should trigger the browser's download mechanism
			await page.evaluate(f"""
				() => {{
					const link = document.createElement('a');
					link.href = '{pdf_url}';
					link.download = '';  // Force download
					link.click();
				}}
			""")
			
		except Exception as e:
			logger.error(f'[DownloadsWatchdog] Error triggering PDF download: {e}')