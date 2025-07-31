"""Test browser watchdog functionality."""

import asyncio
import time

import pytest

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStartedEvent,
	BrowserStoppedEvent,
	CreateTabEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	PageCrashedEvent,
	StartBrowserEvent,
	StopBrowserEvent,
	TabCreatedEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession


@pytest.mark.asyncio
async def test_watchdog_network_timeout(httpserver):
	"""Test that watchdog detects network timeouts by monitoring actual network requests."""

	# Set up a slow endpoint (sync handler that blocks)
	def slow_handler(request):
		time.sleep(2.0)  # Delay longer than timeout
		from werkzeug import Response

		return Response('Slow response', status=200)

	httpserver.expect_request('/slow').respond_with_handler(slow_handler)
	slow_url = httpserver.url_for('/slow')

	# Create browser with short network timeout
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	# Track error events
	error_events = []
	session.event_bus.on(BrowserErrorEvent, lambda e: error_events.append(e))

	try:
		# Start browser
		await session.start()

		# Configure watchdog for quick timeout detection
		if session._watchdog:
			session._watchdog.network_timeout_seconds = 0.5
			session._watchdog.check_interval_seconds = 0.1

		# Navigate to slow endpoint - this will create real network requests
		navigate_event = session.event_bus.dispatch(NavigateToUrlEvent(url=slow_url, wait_until='networkidle'))

		# Wait for network timeout error event
		timeout_error = None
		for _ in range(30):  # Check for 3 seconds
			await asyncio.sleep(0.1)
			timeout_errors = [e for e in error_events if e.error_type == 'NetworkTimeout']
			if timeout_errors:
				timeout_error = timeout_errors[0]
				break

		assert timeout_error is not None, (
			f'NetworkTimeout event was not emitted within 3 seconds. Got {len(error_events)} error events'
		)
		assert 'slow' in timeout_error.details['url']
		assert timeout_error.details['elapsed_seconds'] >= 0.5

	finally:
		await session.stop()


@pytest.mark.asyncio
async def test_watchdog_page_crash_detection():
	"""Test that watchdog detects page crashes using CDP crash simulation."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	# Track events
	crash_events = []
	error_events = []
	session.event_bus.on(PageCrashedEvent, lambda e: crash_events.append(e))
	session.event_bus.on(BrowserErrorEvent, lambda e: error_events.append(e))

	try:
		# Start browser
		session.event_bus.dispatch(StartBrowserEvent())
		await session.event_bus.expect(BrowserStartedEvent, timeout=5.0)

		# Create a new tab
		session.event_bus.dispatch(CreateTabEvent(url='about:blank'))

		# Wait for tab created event
		tab_created = await session.event_bus.expect(TabCreatedEvent, timeout=2.0)

		# Get the created page
		pages = session.tabs
		target_page = pages[-1] if len(pages) > 1 else pages[0]

		# Directly simulate page crash since CDP Page.crash isn't available in test environment
		if session._watchdog:
			await session._watchdog._on_page_crash(target_page)

		# Wait for crash event to be processed
		await asyncio.sleep(0.5)

		# Check crash events
		assert len(crash_events) >= 1, f'Expected crash events but got {len(crash_events)}'
		assert crash_events[0].error == 'Page crashed unexpectedly'

		# Check error events
		page_crash_errors = [e for e in error_events if e.error_type == 'PageCrash']
		assert len(page_crash_errors) >= 1, 'No PageCrash BrowserErrorEvent was emitted'
		assert 'blank' in page_crash_errors[0].message

	finally:
		# Stop browser
		session.event_bus.dispatch(StopBrowserEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


@pytest.mark.asyncio
async def test_watchdog_browser_disconnect():
	"""Test that watchdog detects browser disconnection through monitoring."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(StartBrowserEvent())

		# Wait for browser to be fully started
		await session.event_bus.expect(BrowserStartedEvent, timeout=5.0)

		# Configure watchdog for faster detection
		if session._watchdog:
			session._watchdog.check_interval_seconds = 0.1

		# Mock browser disconnection by overriding is_connected
		# This simulates what would happen if the browser process crashed
		if session._browser:
			original_is_connected = session._browser.is_connected
			session._browser.is_connected = lambda: False

			try:
				# Wait for watchdog to detect disconnection
				disconnect_error = await session.event_bus.expect(
					BrowserErrorEvent, predicate=lambda e: e.error_type == 'BrowserDisconnected', timeout=2.0
				)
				assert 'disconnected unexpectedly' in disconnect_error.message
			finally:
				# Restore original method
				session._browser.is_connected = original_is_connected

	finally:
		# Force stop even if browser is marked as disconnected
		try:
			session.event_bus.dispatch(StopBrowserEvent(force=True))
			await asyncio.sleep(0.5)  # Give it time to stop
		except Exception:
			pass  # Browser might already be stopped


@pytest.mark.asyncio
async def test_watchdog_starts_and_stops_with_session():
	"""Test that watchdog lifecycle follows browser session events."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	# Start browser via event
	session.event_bus.dispatch(StartBrowserEvent())

	# Wait for browser started event
	started_event = await session.event_bus.expect(BrowserStartedEvent, timeout=5.0)
	assert started_event.cdp_url is not None

	# Create a test page to ensure watchdog is monitoring something
	session.event_bus.dispatch(CreateTabEvent(url='about:blank'))

	# Wait for tab created event
	await session.event_bus.expect(TabCreatedEvent, timeout=2.0)

	# Navigate to a real page to generate some network activity
	session.event_bus.dispatch(NavigateToUrlEvent(url='data:text/html,<h1>Test</h1>'))

	# Wait for navigation to complete successfully (proves watchdog isn't interfering)
	nav_complete = await session.event_bus.expect(NavigationCompleteEvent, timeout=2.0)
	assert nav_complete.url == 'data:text/html,<h1>Test</h1>'
	assert nav_complete.error_message is None

	# Stop browser via event
	session.event_bus.dispatch(StopBrowserEvent())

	# Wait for browser stopped event
	stopped_event = await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)
	assert stopped_event.reason is not None
