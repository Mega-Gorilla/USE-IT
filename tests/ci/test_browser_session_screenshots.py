"""
Test that screenshots work correctly in headless browser mode.
"""

import base64

from browser_use.browser import BrowserProfile, BrowserSession


class TestHeadlessScreenshots:
	"""Test screenshot functionality specifically in headless browsers"""

	async def test_screenshot_works_in_headless_mode(self, httpserver):
		"""Explicitly test that screenshots can be captured in headless=True mode"""
		# Create a browser session with headless=True
		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,  # Explicitly set headless mode
				user_data_dir=None,
				keep_alive=False,
			)
		)

		try:
			# Start the session
			await browser_session.start()
			assert browser_session.initialized

			# Set up test page with visible content
			httpserver.expect_request('/').respond_with_data(
				"""<html>
				<head><title>Headless Screenshot Test</title></head>
				<body style="background: white; padding: 20px;">
					<h1 style="color: black;">This is a test page</h1>
					<p style="color: blue;">Testing screenshot capture in headless mode</p>
					<div style="width: 200px; height: 100px; background: red;">Red Box</div>
				</body>
				</html>""",
				content_type='text/html',
			)

			# Navigate to test page
			await browser_session.navigate(httpserver.url_for('/'))

			# Take screenshot
			screenshot_b64 = await browser_session.take_screenshot()

			# Verify screenshot was captured
			assert screenshot_b64 is not None
			assert isinstance(screenshot_b64, str)
			assert len(screenshot_b64) > 0

			# Decode and validate the screenshot
			screenshot_bytes = base64.b64decode(screenshot_b64)

			# Verify PNG signature
			assert screenshot_bytes.startswith(b'\x89PNG\r\n\x1a\n')
			# Should be a reasonable size (not just a blank image)
			assert len(screenshot_bytes) > 5000, f'Screenshot too small: {len(screenshot_bytes)} bytes'

			# Test full page screenshot
			full_page_screenshot = await browser_session.take_screenshot(full_page=True)
			assert full_page_screenshot is not None
			full_page_bytes = base64.b64decode(full_page_screenshot)
			assert full_page_bytes.startswith(b'\x89PNG\r\n\x1a\n')
			assert len(full_page_bytes) > 5000

		finally:
			await browser_session.stop()

	async def test_screenshot_with_state_summary_in_headless(self, httpserver):
		"""Test that get_state_summary includes screenshots in headless mode"""
		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
			)
		)

		try:
			await browser_session.start()

			# Set up test page
			httpserver.expect_request('/').respond_with_data(
				'<html><body><h1>State Summary Test</h1></body></html>',
				content_type='text/html',
			)
			await browser_session.navigate(httpserver.url_for('/'))

			# Get state summary
			state = await browser_session.get_state_summary(cache_clickable_elements_hashes=False)

			# Verify screenshot is included
			assert state.screenshot is not None
			assert isinstance(state.screenshot, str)
			assert len(state.screenshot) > 0

			# Decode and validate
			screenshot_bytes = base64.b64decode(state.screenshot)
			assert screenshot_bytes.startswith(b'\x89PNG\r\n\x1a\n')
			assert len(screenshot_bytes) > 1000

		finally:
			await browser_session.stop()

	async def test_screenshot_graceful_handling_in_headless(self):
		"""Test that screenshot handling works correctly in headless mode even with closed pages"""
		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
			)
		)

		try:
			await browser_session.start()

			# Close all pages to test edge case
			assert browser_session.browser_context is not None
			pages = browser_session.browser_context.pages
			for page in pages:
				await page.close()

			# Browser should auto-create a new page, so screenshot should still work
			screenshot = await browser_session.take_screenshot()
			# Should get a screenshot of the new blank page
			assert screenshot is not None
			assert isinstance(screenshot, str)
			assert len(screenshot) > 0

			# Get state summary should also work
			state = await browser_session.get_state_summary(cache_clickable_elements_hashes=False)
			# Should have a screenshot
			assert state.screenshot is not None
			assert isinstance(state.screenshot, str)

		finally:
			await browser_session.stop()
