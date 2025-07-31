"""Test to verify download detection timing issue"""

import asyncio
import os
import time

import pytest
from werkzeug.wrappers import Response

from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile


@pytest.fixture(scope='function')
async def test_server(httpserver):
	"""Setup test HTTP server with a simple page."""
	html_content = """
	<!DOCTYPE html>
	<html>
	<head>
		<title>Test Page</title>
	</head>
	<body>
		<h1>Test Page</h1>
		<button id="test-button" onclick="document.getElementById('result').innerText = 'Clicked!'">
			Click Me
		</button>
		<p id="result"></p>
		<a href="/download/test.pdf" download>Download PDF</a>
	</body>
	</html>
	"""
	httpserver.expect_request('/').respond_with_data(html_content, content_type='text/html')

	# Set up PDF download with proper headers to force download
	def pdf_handler(request):
		return Response(
			b'PDF content',
			content_type='application/pdf',
			headers={'Content-Disposition': 'attachment; filename="test.pdf"', 'Content-Length': '11'},
		)

	httpserver.expect_request('/download/test.pdf').respond_with_handler(pdf_handler)
	return httpserver


async def test_download_detection_timing(test_server, tmp_path):
	"""Test that download detection adds 5 second delay to clicks when downloads_dir is set."""

	# Test 1: With downloads_dir set (default behavior)
	browser_with_downloads = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			downloads_path=str(tmp_path / 'downloads'),
			user_data_dir=None,
		)
	)

	await browser_with_downloads.start()
	page = await browser_with_downloads.get_current_page()
	await page.goto(test_server.url_for('/'))

	# Get the actual DOM state to find the button
	state = await browser_with_downloads.get_browser_state_with_recovery()

	# Find the button element
	button_node = None
	for elem in state.selector_map.values():
		if elem.tag_name == 'button' and elem.attributes.get('id') == 'test-button':
			button_node = elem
			break

	assert button_node is not None, 'Could not find button element'

	# Time the click
	start_time = time.time()
	result = await browser_with_downloads._click_element_node(button_node)
	duration_with_downloads = time.time() - start_time

	# Verify click worked
	result_text = await page.locator('#result').text_content()
	assert result_text == 'Clicked!'
	assert result is None  # No download happened

	await browser_with_downloads.close()

	# Test 2: With downloads_dir set to empty string (disables download detection)
	browser_no_downloads = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			downloads_path=None,
			user_data_dir=None,
		)
	)

	await browser_no_downloads.start()
	page = await browser_no_downloads.get_current_page()
	await page.goto(test_server.url_for('/'))

	# Clear previous result
	await page.evaluate('document.getElementById("result").innerText = ""')

	# Get the DOM state again for the new browser session
	state = await browser_no_downloads.get_browser_state_with_recovery()

	# Find the button element again
	button_node = None
	for elem in state.selector_map.values():
		if elem.tag_name == 'button' and elem.attributes.get('id') == 'test-button':
			button_node = elem
			break

	assert button_node is not None, 'Could not find button element'

	# Time the click
	start_time = time.time()
	result = await browser_no_downloads._click_element_node(button_node)
	duration_no_downloads = time.time() - start_time

	# Verify click worked
	result_text = await page.locator('#result').text_content()
	assert result_text == 'Clicked!'

	await browser_no_downloads.close()

	# Check timing differences
	print(f'Click with downloads_dir: {duration_with_downloads:.2f}s')
	print(f'Click without downloads_dir: {duration_no_downloads:.2f}s')
	print(f'Difference: {duration_with_downloads - duration_no_downloads:.2f}s')

	# Both should be fast now since we're clicking a button (not a download link)
	assert duration_with_downloads < 8, f'Expected <8s with downloads_dir, got {duration_with_downloads:.2f}s'
	assert duration_no_downloads < 3, f'Expected <3s without downloads_dir, got {duration_no_downloads:.2f}s'


async def test_actual_download_detection(test_server, tmp_path):
	"""Test that actual downloads are detected correctly."""

	downloads_path = tmp_path / 'downloads'
	downloads_path.mkdir()

	browser_session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			downloads_path=str(downloads_path),
			user_data_dir=None,  # Don't use persistent context for now
		)
	)

	await browser_session.start()
	page = await browser_session.get_current_page()
	await page.goto(test_server.url_for('/'))

	# Get the DOM state to find the download link
	state = await browser_session.get_browser_state_with_recovery()

	# Find the download link element
	download_node = None
	for elem in state.selector_map.values():
		if elem.tag_name == 'a' and 'download' in elem.attributes:
			download_node = elem
			break

	assert download_node is not None, 'Could not find download link element'

	# Debug: Log what we're about to click
	print(f'Clicking download link with href: {download_node.attributes.get("href")}')
	print(f'Download link has download attribute: {"download" in download_node.attributes}')
	print(f'Auto download PDFs enabled: {browser_session._auto_download_pdfs}')

	# Since the link has a download attribute, it will trigger a download, not navigation
	# We need to intercept the download event
	start_time = time.time()

	# Listen for download event on the page
	download_started = False
	received_download = None

	async def handle_download(download):
		nonlocal download_started, received_download
		download_started = True
		received_download = download
		print(f'Download started: {download.suggested_filename}')
		# Try saving it manually for debugging
		try:
			# First, let's see the download state
			print(f'Download URL: {download.url}')
			print(f'Download suggested filename: {download.suggested_filename}')

			# Try to get the path where it's being downloaded
			path = await download.path()
			print(f'Download path from browser: {path}')

			# If path exists, copy it to our downloads dir
			if path and os.path.exists(path):
				import shutil

				dest_path = downloads_path / download.suggested_filename
				shutil.copy(path, dest_path)
				print(f'Copied download from {path} to {dest_path}')
			else:
				print(f'Download path does not exist: {path}')
		except Exception as e:
			print(f'Error handling download: {e}')

	page.on('download', handle_download)

	# Click the download link
	await browser_session._click_element_node(download_node)
	duration = time.time() - start_time

	print(f'Click completed in {duration:.2f}s')

	# Wait for download to be processed
	await asyncio.sleep(2.0)

	# Check if we're still on the same page (download attribute prevents navigation)
	current_url = page.url
	print(f'Current URL after click: {current_url}')
	print(f'Download started: {download_started}')

	# Wait a bit more
	await asyncio.sleep(1.0)

	# Check the downloaded files using the browser_session property
	downloaded_files = browser_session.downloaded_files

	# Debug: check the downloads directory
	print(f'Downloads directory: {downloads_path}')
	print(f'Directory exists: {os.path.exists(downloads_path)}')
	if os.path.exists(downloads_path):
		files = os.listdir(downloads_path)
		print(f'Files in downloads directory: {files}')

	# Should have at least one downloaded file
	assert len(downloaded_files) > 0, f'Should have downloaded files. Downloads dir: {downloads_path}, Files: {downloaded_files}'

	# Check the most recent download
	latest_download = downloaded_files[0]  # Files are sorted by newest first
	assert 'test.pdf' in latest_download
	assert os.path.exists(latest_download)

	# Verify the file size is correct (we sent 'PDF content' which is 11 bytes)
	file_size = os.path.getsize(latest_download)
	assert file_size == 11, f'Expected 11 bytes, got {file_size}'

	# Should be relatively fast since download is detected
	assert duration < 2.0, f'Download click took {duration:.2f}s, expected <2s'

	await browser_session.close()
