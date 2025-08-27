"""
Test script for BrowserSession storage state functionality and event-driven storage state.

Tests cover:
- Loading storage state on browser start
- Saving storage state (including cookies and local storage)
- Verifying storage state is applied to browser context
- NEW: Event-driven storage state operations
"""

import json
import logging
import tempfile
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

# Set up test logging
logger = logging.getLogger('browser_session_cookie_tests')


class TestBrowserSessionStorageState:
	"""Tests for BrowserSession storage state loading and saving functionality."""

	@pytest.fixture
	async def temp_storage_state_file(self):
		"""Create a temporary storage state file with test cookies and local storage."""
		with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
			storage_state = {
				'cookies': [
					{
						'name': 'test_cookie',
						'value': 'test_value',
						'domain': '127.0.0.1',
						'path': '/',
						'expires': -1,
						'httpOnly': False,
						'secure': False,
						'sameSite': 'Lax',
					},
					{
						'name': 'session_cookie',
						'value': 'session_12345',
						'domain': '127.0.0.1',
						'path': '/',
						'expires': -1,
						'httpOnly': True,
						'secure': False,
						'sameSite': 'Lax',
					},
				],
				'origins': [],  # Could add localStorage/sessionStorage data here
			}
			json.dump(storage_state, f)
			temp_path = Path(f.name)

		yield temp_path

		# Cleanup
		temp_path.unlink(missing_ok=True)

	@pytest.fixture
	async def browser_profile_with_storage_state(self, temp_storage_state_file):
		"""Create a BrowserProfile with storage_state set."""
		profile = BrowserProfile(headless=True, user_data_dir=None, storage_state=temp_storage_state_file)
		yield profile

	@pytest.fixture
	async def browser_session_with_storage_state(self, browser_profile_with_storage_state):
		"""Create a BrowserSession with storage state configured."""
		session = BrowserSession(browser_profile=browser_profile_with_storage_state)
		yield session
		# Cleanup
		try:
			await session.stop()
		except Exception:
			pass

	@pytest.fixture
	def http_server(self, httpserver: HTTPServer):
		"""Set up HTTP server with test endpoints."""
		# Endpoint that shows cookies
		httpserver.expect_request('/cookies').respond_with_data(
			"""
			<html>
			<body>
				<h1>Cookie Test Page</h1>
				<script>
					document.write('<p>Cookies: ' + document.cookie + '</p>');
				</script>
			</body>
			</html>
			""",
			content_type='text/html',
		)
		return httpserver


# class TestStorageStateEventSystem:
# 	"""Tests for NEW event-driven storage state operations."""

# 	async def test_save_storage_state_event_dispatching(self, httpserver: HTTPServer, tmp_path: Path):
# 		"""Test that SaveStorageStateEvent can be dispatched directly."""
# 		# Create temporary storage file
# 		storage_file = tmp_path / 'event_test_storage.json'

# 		# Set up test page with cookies
# 		httpserver.expect_request('/cookie-test').respond_with_data(
# 			'<html><body><h1>Storage Event Test</h1></body></html>',
# 			content_type='text/html',
# 			headers={'Set-Cookie': 'test_event_cookie=event_value; Path=/'},
# 		)

# 		browser_session = BrowserSession(
# 			browser_profile=BrowserProfile(headless=True, user_data_dir=None, storage_state=storage_file, keep_alive=False)
# 		)

# 		try:
# 			await browser_session.start()

# 			# Navigate to set cookies
# 			event = browser_session.event_bus.dispatch(NavigateToUrlEvent(url=httpserver.url_for('/cookie-test')))
# 			await event
# 			await event.event_result(raise_if_any=True, raise_if_none=False)

# 			# Dispatch SaveStorageStateEvent directly
# 			save_event = browser_session.event_bus.dispatch(SaveStorageStateEvent())
# 			await save_event

# 			# Verify storage file was created
# 			assert storage_file.exists(), 'Storage state file should be created by event handler'

# 			# Verify file contains cookies
# 			storage_data = json.loads(storage_file.read_text())
# 			assert 'cookies' in storage_data

# 		finally:
# 			await browser_session.kill()
