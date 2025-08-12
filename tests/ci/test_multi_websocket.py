"""Test multi-WebSocket CDP connections."""

import asyncio
import logging

import pytest
from pytest_httpserver import HTTPServer

from browser_use.browser.session import BrowserSession

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@pytest.fixture
def test_html(httpserver: HTTPServer):
	"""Set up test HTML pages."""
	# Main page HTML
	main_html = """
	<!DOCTYPE html>
	<html>
	<head><title>Main Page</title></head>
	<body>
		<h1 id="main-title">Main Page</h1>
		<button id="test-button">Click Me</button>
		<a href="/page2.html" target="_blank">Open Page 2</a>
	</body>
	</html>
	"""
	
	# Second page HTML
	page2_html = """
	<!DOCTYPE html>
	<html>
	<head><title>Page 2</title></head>
	<body>
		<h1 id="page2-title">Page 2</h1>
		<input id="test-input" type="text" placeholder="Enter text">
	</body>
	</html>
	"""
	
	httpserver.expect_request("/").respond_with_data(main_html, content_type="text/html")
	httpserver.expect_request("/page2.html").respond_with_data(page2_html, content_type="text/html")
	
	return httpserver.url_for("/")


@pytest.mark.asyncio
async def test_shared_websocket_default(test_html):
	"""Test that default behavior uses shared WebSocket."""
	browser_session = BrowserSession()
	
	try:
		await browser_session.start()
		
		# Navigate to test page
		await browser_session._cdp_navigate(test_html)
		
		# Create new tab
		target_id = await browser_session._cdp_create_new_page(test_html + "page2.html")
		
		# Get sessions for both targets (explicitly request shared WebSocket)
		session1 = browser_session.agent_focus
		session2 = await browser_session.get_or_create_cdp_session(target_id, focus=False, new_socket=False)
		
		# Both should use the same CDP client
		assert session1.cdp_client is session2.cdp_client
		assert not session1.owns_cdp_client
		assert not session2.owns_cdp_client
		
		logger.info("✅ Default behavior: Both sessions share the same WebSocket connection")
		
	finally:
		await browser_session.kill()


@pytest.mark.asyncio
async def test_dedicated_websocket_per_target(test_html):
	"""Test that new_socket=True creates separate WebSocket connections."""
	browser_session = BrowserSession(headless=True, user_data_dir=None)
	
	try:
		await browser_session.start()
		
		# Navigate to test page
		await browser_session._cdp_navigate(test_html)
		
		# Create new tab
		target_id = await browser_session._cdp_create_new_page(test_html + "page2.html")
		
		# Get session for first target (uses shared WebSocket)
		session1 = browser_session.agent_focus
		
		# Get session for second target with dedicated WebSocket
		session2 = await browser_session.get_or_create_cdp_session(
			target_id, 
			focus=False, 
			new_socket=True
		)
		
		# Sessions should use different CDP clients
		assert session1.cdp_client is not session2.cdp_client
		assert not session1.owns_cdp_client
		assert session2.owns_cdp_client
		
		# Both sessions should work independently
		# Test session1
		result1 = await session1.cdp_client.send.Runtime.evaluate(
			params={'expression': 'document.title'},
			session_id=session1.session_id
		)
		assert result1['result']['value'] == 'Main Page'
		
		# Test session2
		result2 = await session2.cdp_client.send.Runtime.evaluate(
			params={'expression': 'document.title'},
			session_id=session2.session_id
		)
		assert result2['result']['value'] == 'Page 2'
		
		logger.info("✅ Multi-WebSocket: Each target has its own WebSocket connection")
		
	finally:
		await browser_session.kill()


@pytest.mark.asyncio
async def test_websocket_cleanup(test_html):
	"""Test that WebSocket connections are properly cleaned up."""
	browser_session = BrowserSession()
	
	try:
		await browser_session.start()
		
		# Create multiple tabs with dedicated WebSockets
		target_ids = []
		for i in range(3):
			target_id = await browser_session._cdp_create_new_page(f"{test_html}#tab{i}")
			target_ids.append(target_id)
			
			# Create session with dedicated WebSocket
			session = await browser_session.get_or_create_cdp_session(
				target_id,
				focus=False,
				new_socket=True
			)
			assert session.owns_cdp_client
		
		# Should have 3 sessions in the pool (plus the initial one)
		assert len(browser_session._cdp_session_pool) >= 3
		
		# Reset should clean up all WebSocket connections
		await browser_session.reset()
		
		# Pool should be empty
		assert len(browser_session._cdp_session_pool) == 0
		
		logger.info("✅ Cleanup: All WebSocket connections properly disconnected")
		
	finally:
		await browser_session.kill()


@pytest.mark.asyncio
async def test_mixed_websocket_sessions(test_html):
	"""Test mixing shared and dedicated WebSocket sessions."""
	browser_session = BrowserSession()
	
	try:
		await browser_session.start()
		
		# Create multiple tabs
		target_ids = []
		for i in range(4):
			target_id = await browser_session._cdp_create_new_page(f"{test_html}#tab{i}")
			target_ids.append(target_id)
		
		# Create sessions with mixed WebSocket types
		session_shared1 = await browser_session.get_or_create_cdp_session(
			target_ids[0], focus=False, new_socket=False
		)
		session_dedicated1 = await browser_session.get_or_create_cdp_session(
			target_ids[1], focus=False, new_socket=True
		)
		session_shared2 = await browser_session.get_or_create_cdp_session(
			target_ids[2], focus=False, new_socket=False
		)
		session_dedicated2 = await browser_session.get_or_create_cdp_session(
			target_ids[3], focus=False, new_socket=True
		)
		
		# Verify ownership
		assert not session_shared1.owns_cdp_client
		assert session_dedicated1.owns_cdp_client
		assert not session_shared2.owns_cdp_client
		assert session_dedicated2.owns_cdp_client
		
		# Shared sessions should use the same client
		assert session_shared1.cdp_client is session_shared2.cdp_client
		
		# Dedicated sessions should use different clients
		assert session_dedicated1.cdp_client is not session_dedicated2.cdp_client
		assert session_dedicated1.cdp_client is not session_shared1.cdp_client
		
		logger.info("✅ Mixed mode: Shared and dedicated WebSockets work together")
		
	finally:
		await browser_session.kill()


if __name__ == "__main__":
	# Run with: python -m pytest tests/ci/test_multi_websocket.py -v
	pytest.main([__file__, "-v", "-s"])
