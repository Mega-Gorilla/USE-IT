import asyncio

import pytest
from pytest_httpserver import HTTPServer

from browser_use.agent.views import ActionModel, ActionResult
from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.controller.service import Controller
from browser_use.controller.views import (
	GoToUrlAction,
	ScrollAction,
)


@pytest.fixture(scope='session')
def http_server():
	"""Create and provide a test HTTP server that serves static content."""
	server = HTTPServer()
	server.start()

	# Add routes for common test pages
	server.expect_request('/').respond_with_data(
		'<html><head><title>Test Home Page</title></head><body><h1>Test Home Page</h1><p>Welcome to the test site</p></body></html>',
		content_type='text/html',
	)

	server.expect_request('/scrollable').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head>
			<title>Scrollable Page</title>
			<style>
				body { margin: 0; padding: 20px; }
				.content { height: 3000px; background: linear-gradient(to bottom, #f0f0f0, #333); }
				.marker { padding: 20px; background: #007bff; color: white; margin: 500px 0; }
			</style>
		</head>
		<body>
			<h1>Scrollable Test Page</h1>
			<div class="content">
				<div class="marker" id="marker1">Marker 1</div>
				<div class="marker" id="marker2">Marker 2</div>
				<div class="marker" id="marker3">Marker 3</div>
			</div>
		</body>
		</html>
		""",
		content_type='text/html',
	)

	yield server
	server.stop()


@pytest.fixture(scope='session')
def base_url(http_server):
	"""Return the base URL for the test HTTP server."""
	return f'http://{http_server.host}:{http_server.port}'


@pytest.fixture(scope='module')
async def browser_session():
	"""Create and provide a Browser instance with security disabled."""
	profile = BrowserProfile(headless=True, disable_security=True, cross_origin_iframes=False)
	session = BrowserSession(browser_profile=profile)
	await session.start()
	yield session
	await session.kill()


@pytest.fixture
def controller():
	"""Create and provide a Controller instance."""
	return Controller()


class TestScrollActions:
	"""Test scroll-related actions and events."""

	async def test_scroll_actions(self, controller, browser_session, base_url, http_server):
		"""Test basic scroll action functionality."""

		# Navigate to scrollable page
		goto_action = {'go_to_url': GoToUrlAction(url=f'{base_url}/scrollable', new_tab=False)}

		class GoToUrlActionModel(ActionModel):
			go_to_url: GoToUrlAction | None = None

		await controller.act(GoToUrlActionModel(**goto_action), browser_session)

		# Test 1: Basic page scroll down
		scroll_action = {'scroll': ScrollAction(down=True, num_pages=1.0)}

		class ScrollActionModel(ActionModel):
			scroll: ScrollAction | None = None

		result = await controller.act(ScrollActionModel(**scroll_action), browser_session)

		# Verify scroll down succeeded
		assert isinstance(result, ActionResult)
		assert result.error is None, f'Scroll down failed: {result.error}'
		assert result.extracted_content is not None
		assert 'Scrolled down' in result.extracted_content
		assert 'the page' in result.extracted_content
		assert result.include_in_memory is True

		# Test 2: Basic page scroll up
		scroll_up_action = {'scroll': ScrollAction(down=False, num_pages=0.5)}
		result = await controller.act(ScrollActionModel(**scroll_up_action), browser_session)

		assert isinstance(result, ActionResult)
		assert result.error is None, f'Scroll up failed: {result.error}'
		assert result.extracted_content is not None
		assert 'Scrolled up' in result.extracted_content
		assert '0.5 pages' in result.extracted_content

		# Test 3: Test with invalid element index (should error)
		invalid_scroll_action = {'scroll': ScrollAction(down=True, num_pages=1.0, index=999)}
		result = await controller.act(ScrollActionModel(**invalid_scroll_action), browser_session)

		# This should fail with error about element not found
		assert isinstance(result, ActionResult)
		assert result.error is not None, 'Expected error for invalid element index'
		assert 'Element index 999 not found' in result.error or 'Failed to scroll' in result.error

		# Test 4: Model parameter validation
		scroll_with_index = ScrollAction(down=True, num_pages=1.0, index=5)
		assert scroll_with_index.down is True
		assert scroll_with_index.num_pages == 1.0
		assert scroll_with_index.index == 5

		scroll_without_index = ScrollAction(down=False, num_pages=0.25)
		assert scroll_without_index.down is False
		assert scroll_without_index.num_pages == 0.25
		assert scroll_without_index.index is None

	async def test_scroll_with_cross_origin_disabled(self, browser_session, base_url):
		"""Test that scroll works when cross_origin_iframes is disabled."""
		from browser_use.browser.events import ScrollEvent
		
		# Navigate to a page
		await browser_session._cdp_navigate(f'{base_url}/scrollable')
		await asyncio.sleep(0.5)
		
		# Test simple scroll - should not hang
		event = browser_session.event_bus.dispatch(ScrollEvent(direction='down', amount=500))
		result = await asyncio.wait_for(event, timeout=3.0)
		assert result is not None
		
		# Test scroll up
		event = browser_session.event_bus.dispatch(ScrollEvent(direction='up', amount=200))
		result = await asyncio.wait_for(event, timeout=3.0)
		assert result is not None

	async def test_scroll_event_directly(self, browser_session):
		"""Test ScrollEvent directly through the event bus."""
		from browser_use.browser.events import ScrollEvent
		
		# Test scroll on about:blank (should work)
		event = browser_session.event_bus.dispatch(ScrollEvent(direction='down', amount=100))
		result = await asyncio.wait_for(event, timeout=2.0)
		event_result = await result.event_result()
		assert event_result is not None
		assert event_result.get('success') is True

	async def test_scroll_with_element_node(self, browser_session, base_url, http_server):
		"""Test scrolling a specific element via node parameter."""
		from browser_use.browser.events import ScrollEvent
		
		# Add a page with a scrollable div element
		http_server.expect_request('/scrollable-div').respond_with_data(
			"""
			<!DOCTYPE html>
			<html>
			<head>
				<title>Scrollable Div Test</title>
				<style>
					body { margin: 0; padding: 20px; }
					#scrollable-container {
						width: 400px;
						height: 300px;
						overflow-y: scroll;
						border: 2px solid #333;
						background: #f0f0f0;
					}
					.inner-content {
						height: 1500px;
						background: linear-gradient(to bottom, #f0f0f0, #333);
						padding: 20px;
					}
					.marker {
						padding: 20px;
						background: #007bff;
						color: white;
						margin: 200px 0;
					}
				</style>
			</head>
			<body>
				<h1>Page with Scrollable Div</h1>
				<div id="scrollable-container">
					<div class="inner-content">
						<p>Start of scrollable content</p>
						<div class="marker">Marker 1</div>
						<div class="marker">Marker 2</div>
						<div class="marker">Marker 3</div>
						<p>End of scrollable content</p>
					</div>
				</div>
				<p>Content outside scrollable div</p>
			</body>
			</html>
			""",
			content_type='text/html',
		)
		
		# Navigate to the page with scrollable div
		await browser_session._cdp_navigate(f'{base_url}/scrollable-div')
		await asyncio.sleep(0.5)
		
		# Get browser state to find the scrollable element
		state = await browser_session.get_browser_state_summary()
		
		# Find the scrollable container element in the selector map
		scrollable_node = None
		for index, node in state.selector_map.items():
			# Look for the div with id="scrollable-container"
			if (hasattr(node, 'attributes') and 
				node.attributes.get('id') == 'scrollable-container'):
				scrollable_node = node
				break
		
		assert scrollable_node is not None, "Could not find scrollable-container element"
		
		# Test scrolling the specific element down
		event = browser_session.event_bus.dispatch(
			ScrollEvent(direction='down', amount=300, node=scrollable_node)
		)
		result = await asyncio.wait_for(event, timeout=3.0)
		event_result = await result.event_result()
		assert event_result is not None
		assert event_result.get('success') is True
		
		# Test scrolling the specific element up
		event = browser_session.event_bus.dispatch(
			ScrollEvent(direction='up', amount=150, node=scrollable_node)
		)
		result = await asyncio.wait_for(event, timeout=3.0)
		event_result = await result.event_result()
		assert event_result is not None
		assert event_result.get('success') is True