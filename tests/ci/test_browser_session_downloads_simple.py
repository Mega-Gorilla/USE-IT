"""Simple test to verify basic download functionality"""

import asyncio
import os
from pathlib import Path
import tempfile

import pytest
from playwright.async_api import async_playwright

from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile


async def test_simple_playwright_download():
	"""Test basic Playwright download functionality without browser-use"""
	
	async with async_playwright() as p:
		# Create temp directory for downloads
		with tempfile.TemporaryDirectory() as tmpdir:
			downloads_path = Path(tmpdir) / 'downloads'
			downloads_path.mkdir()
			
			# Launch browser
			browser = await p.chromium.launch(
				headless=True
			)
			
			# Create context with downloads enabled
			context = await browser.new_context(
				accept_downloads=True
			)
			
			page = await context.new_page()
			
			# Create a simple HTML page with download link
			html_content = """
			<!DOCTYPE html>
			<html>
			<body>
				<a href="data:text/plain;base64,SGVsbG8gV29ybGQ=" download="test.txt">Download Test File</a>
			</body>
			</html>
			"""
			
			await page.set_content(html_content)
			
			# Set up download promise
			async with page.expect_download() as download_info:
				await page.click('a[download]')
				download = await download_info.value
			
			# Save the download
			save_path = downloads_path / download.suggested_filename
			await download.save_as(str(save_path))
			
			# Verify file exists
			assert save_path.exists()
			assert save_path.read_text() == "Hello World"
			
			print(f"✅ Basic Playwright download test passed!")
			
			await context.close()
			await browser.close()


async def test_browser_use_download_with_data_url():
	"""Test browser-use download with data URL"""
	
	with tempfile.TemporaryDirectory() as tmpdir:
		downloads_path = Path(tmpdir) / 'downloads'
		downloads_path.mkdir()
		
		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				downloads_path=str(downloads_path),
				user_data_dir=None,
			)
		)
		
		await browser_session.start()
		page = await browser_session.get_current_page()
		
		# Create a simple HTML page with download link using data URL
		html_content = """
		<!DOCTYPE html>
		<html>
		<body>
			<h1>Download Test</h1>
			<a id="download-link" href="data:text/plain;base64,SGVsbG8gQnJvd3NlclVzZQ==" download="browseruse.txt">Download BrowserUse File</a>
		</body>
		</html>
		"""
		
		await page.set_content(html_content)
		
		# Wait a moment for DOM to be ready
		await asyncio.sleep(0.5)
		
		# Click the download link directly via Playwright
		# This bypasses browser_session's click handling to test raw functionality
		async with page.expect_download() as download_info:
			await page.click('#download-link')
			download = await download_info.value
		
		# Save the download
		save_path = downloads_path / download.suggested_filename
		await download.save_as(str(save_path))
		
		# Verify file exists
		assert save_path.exists()
		assert save_path.read_text() == "Hello BrowserUse"
		
		print(f"✅ BrowserUse data URL download test passed!")
		
		# Check if browser_session sees the downloaded file
		downloaded_files = browser_session.downloaded_files
		print(f"Downloaded files seen by browser_session: {downloaded_files}")
		
		await browser_session.stop()


if __name__ == "__main__":
	import sys
	if len(sys.argv) > 1 and sys.argv[1] == "playwright":
		asyncio.run(test_simple_playwright_download())
	elif len(sys.argv) > 1 and sys.argv[1] == "browseruse":
		asyncio.run(test_browser_use_download_with_data_url())
	else:
		print("Usage: python test_browser_session_downloads_simple.py [playwright|browseruse]")