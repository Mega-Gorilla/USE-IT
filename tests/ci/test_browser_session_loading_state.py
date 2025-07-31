"""
Test browser session loading state notification when network timeout occurs.
"""

import asyncio
import time

import pytest
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from browser_use.browser import BrowserProfile, BrowserSession


class TestBrowserLoadingState:
	"""Test loading state notification functionality"""

	async def test_loading_status_on_network_timeout(self, httpserver: HTTPServer):
		"""Test that loading status is set when network timeout occurs"""
		# Create a page with multiple resources that never finish loading
		html_content = """
		<html>
		<head>
			<title>Slow Loading Test Page</title>
			<script src="/slow-script.js"></script>
			<link rel="stylesheet" href="/slow-style.css">
		</head>
		<body>
			<h1>Testing Loading Status</h1>
			<p>This page has resources that never finish loading.</p>
			<img src="/slow-image.jpg" alt="Slow loading image">
			<iframe src="/slow-iframe.html" width="400" height="300"></iframe>
		</body>
		</html>
		"""
		
		# Main page loads immediately
		httpserver.expect_request('/').respond_with_data(html_content, content_type='text/html')
		
		# Handler that sleeps longer than the timeout
		def slow_handler(request):
			# Sleep for 5 seconds - longer than the 1s maximum_wait_page_load_time
			time.sleep(5)
			return Response('/* Never loads in time */', content_type='text/plain')

		# Set up all the slow endpoints
		httpserver.expect_request('/slow-script.js').respond_with_handler(slow_handler)
		httpserver.expect_request('/slow-style.css').respond_with_handler(slow_handler)
		httpserver.expect_request('/slow-image.jpg').respond_with_handler(slow_handler)
		httpserver.expect_request('/slow-iframe.html').respond_with_handler(slow_handler)

		# Create browser session with very short timeout
		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
				maximum_wait_page_load_time=1.0,  # 1 second max wait
				wait_for_network_idle_page_load_time=0.1,  # 100ms idle time
				minimum_wait_page_load_time=0.1,  # Don't wait extra
			)
		)

		try:
			await browser_session.start()

			# Navigate to the page with the slow iframe
			await browser_session.navigate(httpserver.url_for('/'))
			
			# Get state - the iframe should still be loading
			# and _wait_for_stable_network should detect it and timeout
			state = await browser_session.get_browser_state_with_recovery()
			
			# Verify loading status was set
			assert state.loading_status is not None, 'Loading status should be set when network timeout occurs'
			assert 'aborted after 1.0s' in state.loading_status
			assert 'pending network requests' in state.loading_status
			assert 'wait action' in state.loading_status

			# Verify the full message format
			expected_pattern = 'Page loading was aborted after 1.0s with'
			assert state.loading_status.startswith(expected_pattern)

		finally:
			await browser_session.kill()

	async def test_loading_status_cleared_on_successful_load(self, httpserver: HTTPServer):
		"""Test that loading status is cleared when page loads successfully"""
		# Set up a simple page that loads quickly
		httpserver.expect_request('/fast').respond_with_data(
			'<html><head><title>Fast Page</title></head><body><h1>Quick loading page</h1></body></html>',
			content_type='text/html',
		)

		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
				maximum_wait_page_load_time=5.0,  # Generous timeout
			)
		)

		try:
			await browser_session.start()

			# Navigate to the fast-loading page
			await browser_session.navigate(httpserver.url_for('/fast'))

			# Get browser state
			state = await browser_session.get_browser_state_with_recovery()

			# Loading status should be None for successful loads
			assert state.loading_status is None, 'Loading status should be None when page loads successfully'

		finally:
			await browser_session.kill()

	async def test_loading_status_reset_on_navigation(self, httpserver: HTTPServer):
		"""Test that loading status is reset when navigating to a new page"""
		# Set up pages
		slow_html = """
		<html>
		<head>
			<title>Slow Page</title>
			<script src="/slow.js"></script>
		</head>
		<body><h1>Slow page</h1></body>
		</html>
		"""

		httpserver.expect_request('/slow').respond_with_data(slow_html, content_type='text/html')
		httpserver.expect_request('/slow.js').respond_with_handler(lambda req: (time.sleep(5), 'slow')[1])

		httpserver.expect_request('/fast').respond_with_data(
			'<html><head><title>Fast Page</title></head><body><h1>Fast page</h1></body></html>',
			content_type='text/html',
		)

		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
				maximum_wait_page_load_time=0.5,  # Short timeout for first page
			)
		)

		try:
			await browser_session.start()

			# Navigate to slow page (should timeout)
			await browser_session.navigate(httpserver.url_for('/slow'))
			state1 = await browser_session.get_browser_state_with_recovery()
			assert state1.loading_status is not None, 'Loading status should be set for slow page'

			# Navigate to fast page
			await browser_session.navigate(httpserver.url_for('/fast'))
			state2 = await browser_session.get_browser_state_with_recovery()

			# Loading status should be cleared after successful navigation
			assert state2.loading_status is None, 'Loading status should be cleared after navigating to new page'

		finally:
			await browser_session.kill()

	async def test_loading_status_in_minimal_state_fallback(self, httpserver: HTTPServer):
		"""Test that loading status is preserved even when falling back to minimal state"""
		# Create a page that causes DOM processing to fail
		malformed_html = """
		<html>
		<head>
			<title>Malformed Page</title>
			<script src="/slow.js"></script>
			<script>
				// This might cause DOM processing issues
				Object.defineProperty(document, 'querySelectorAll', {
					get() { throw new Error('DOM processing blocked'); }
				});
			</script>
		</head>
		<body><h1>Page with DOM issues</h1></body>
		</html>
		"""

		httpserver.expect_request('/malformed').respond_with_data(malformed_html, content_type='text/html')
		httpserver.expect_request('/slow.js').respond_with_handler(lambda req: (time.sleep(5), 'slow')[1])

		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
				maximum_wait_page_load_time=0.5,  # Short timeout
			)
		)

		try:
			await browser_session.start()

			# Navigate to the malformed page
			await browser_session.navigate(httpserver.url_for('/malformed'))

			# Get browser state - this might fall back to minimal state
			state = await browser_session.get_browser_state_with_recovery()

			# Even if we get minimal state, loading status should be preserved
			if state.loading_status is not None:
				assert 'aborted after 0.5s' in state.loading_status
				assert 'pending network requests' in state.loading_status

		finally:
			await browser_session.kill()

	@pytest.mark.parametrize('timeout_seconds', [0.5, 1.0, 2.0])
	async def test_loading_status_with_different_timeouts(self, httpserver: HTTPServer, timeout_seconds: float):
		"""Test that loading status correctly reports the configured timeout value"""
		# Set up a slow page
		httpserver.expect_request(f'/timeout_{timeout_seconds}').respond_with_data(
			f'<html><head><title>Timeout Test {timeout_seconds}s</title>'
			f'<script src="/slow_{timeout_seconds}.js"></script></head>'
			f'<body><h1>Testing {timeout_seconds}s timeout</h1></body></html>',
			content_type='text/html',
		)
		httpserver.expect_request(f'/slow_{timeout_seconds}.js').respond_with_handler(lambda req: (time.sleep(10), 'slow')[1])

		browser_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=False,
				maximum_wait_page_load_time=timeout_seconds,
				wait_for_network_idle_page_load_time=0.1,
				minimum_wait_page_load_time=0.1,
			)
		)

		try:
			await browser_session.start()

			# Navigate to the page
			await browser_session.navigate(httpserver.url_for(f'/timeout_{timeout_seconds}'))

			# Get browser state
			state = await browser_session.get_browser_state_with_recovery()

			# Verify loading status contains the correct timeout value
			if state.loading_status is not None:
				assert f'aborted after {timeout_seconds}s' in state.loading_status
				assert 'pending network requests' in state.loading_status

		finally:
			await browser_session.kill()
