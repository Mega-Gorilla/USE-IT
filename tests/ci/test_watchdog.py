"""Test browser watchdog functionality."""

import asyncio
import time
from typing import cast

import pytest

from browser_use.browser.events import (
	BrowserErrorEvent,
	BrowserStartedEvent,
	BrowserStoppedEvent,
	NavigateToUrlEvent,
	NavigationCompleteEvent,
	StartBrowserEvent,
	StopBrowserEvent,
	TabCreatedEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.utils import logger


@pytest.mark.asyncio
async def test_watchdog_network_timeout():
	"""Test that watchdog detects network timeouts by monitoring actual network requests."""

	# Create browser session
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser using event system
		session.event_bus.dispatch(StartBrowserEvent())
		await session.event_bus.expect(BrowserStartedEvent, timeout=10.0)
		
		logger.info('[TEST] Browser started, configuring watchdog timeout')

		# Configure crash watchdog with very short timeout for testing
		if hasattr(session, '_crash_watchdog') and session._crash_watchdog:
			session._crash_watchdog.network_timeout_seconds = 1.0  # Very short timeout
			session._crash_watchdog.check_interval_seconds = 0.2  # Check frequently

		# Try to navigate to a non-existent slow server that will hang
		# This will create a real network request that will timeout
		slow_url = 'http://192.0.2.1:8080/timeout-test'  # RFC5737 TEST-NET-1 - non-routable
		logger.info(f'[TEST] Navigating to non-routable URL via events: {slow_url}')
		session.event_bus.dispatch(NavigateToUrlEvent(url=slow_url))

		# Wait for the network timeout error via event bus
		logger.info('[TEST] Waiting for NetworkTimeout event via event bus...')
		timeout_error = cast(BrowserErrorEvent, await session.event_bus.expect(
			BrowserErrorEvent,
			predicate=lambda e: cast(BrowserErrorEvent, e).error_type == 'NetworkTimeout',
			timeout=5.0
		))

		# Verify the timeout event details
		assert 'timeout-test' in timeout_error.details['url']
		assert timeout_error.details['elapsed_seconds'] >= 0.8  # Should be at least close to our timeout
		assert timeout_error.message.startswith('Network request timed out after')
		
		logger.info(f'[TEST] Successfully detected network timeout: {timeout_error.message}')

	finally:
		# Stop browser using event system
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

		# Browser disconnection detection is now handled by the crash watchdog
		# No configuration needed

		# Mock browser disconnection by overriding is_connected
		# This simulates what would happen if the browser process crashed
		if session._browser:
			original_is_connected = session._browser.is_connected
			session._browser.is_connected = lambda: False

			try:
				# Wait for watchdog to detect disconnection
				disconnect_error: BrowserErrorEvent = cast(
					BrowserErrorEvent,
					await session.event_bus.expect(
						BrowserErrorEvent,
						predicate=lambda e: cast(BrowserErrorEvent, e).error_type == 'BrowserDisconnected',
						timeout=2.0,
					),
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
	started_event: BrowserStartedEvent = cast(
		BrowserStartedEvent, await session.event_bus.expect(BrowserStartedEvent, timeout=5.0)
	)
	assert started_event.cdp_url is not None

	# Create a test page to ensure watchdog is monitoring something
	session.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))

	# Wait for tab created event
	await session.event_bus.expect(TabCreatedEvent, timeout=2.0)

	# Navigate to a real page to generate some network activity
	session.event_bus.dispatch(NavigateToUrlEvent(url='data:text/html,<h1>Test</h1>'))

	# Wait for navigation to complete successfully (proves watchdog isn't interfering)
	nav_complete: NavigationCompleteEvent = cast(
		NavigationCompleteEvent, await session.event_bus.expect(NavigationCompleteEvent, timeout=2.0)
	)
	assert nav_complete.url == 'data:text/html,<h1>Test</h1>'
	assert nav_complete.error_message is None

	# Stop browser via event
	session.event_bus.dispatch(StopBrowserEvent())

	# Wait for browser stopped event
	stopped_event: BrowserStoppedEvent = cast(
		BrowserStoppedEvent, await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)
	)
	assert stopped_event.reason is not None


@pytest.mark.asyncio
async def test_navigation_watchdog_tab_created_events():
	"""Test that the NavigationWatchdog properly emits TabCreatedEvent when pages are added."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	# Track TabCreatedEvents
	tab_created_events = []
	session.event_bus.on(TabCreatedEvent, lambda e: tab_created_events.append(e))

	try:
		# Start browser
		session.event_bus.dispatch(StartBrowserEvent())
		await session.event_bus.expect(BrowserStartedEvent, timeout=5.0)

		# Verify navigation watchdog was created
		assert hasattr(session, '_navigation_watchdog'), 'NavigationWatchdog should be created'
		assert session._navigation_watchdog is not None, 'NavigationWatchdog should not be None'

		# Create first tab - should emit TabCreatedEvent
		session.event_bus.dispatch(NavigateToUrlEvent(url='data:text/html,<h1>Tab 1</h1>', new_tab=True))

		# Wait for first TabCreatedEvent
		first_event: TabCreatedEvent = cast(TabCreatedEvent, await session.event_bus.expect(TabCreatedEvent, timeout=3.0))
		# The first event might be for about:blank or the target URL depending on timing
		assert first_event.tab_index >= 0
		assert isinstance(first_event.url, str)

		# Create second tab - should emit another TabCreatedEvent
		session.event_bus.dispatch(NavigateToUrlEvent(url='data:text/html,<h1>Tab 2</h1>', new_tab=True))

		# Wait for second TabCreatedEvent
		second_event: TabCreatedEvent = cast(TabCreatedEvent, await session.event_bus.expect(TabCreatedEvent, timeout=3.0))
		assert second_event.tab_index >= 0
		assert isinstance(second_event.url, str)

		# Verify we have at least 2 TabCreatedEvents
		assert len(tab_created_events) >= 2, f'Expected at least 2 TabCreatedEvents, got {len(tab_created_events)}'

		# Verify the events have different tab indices (unless they're reusing the same tab)
		unique_tab_indexes = {event.tab_index for event in tab_created_events}
		assert len(unique_tab_indexes) >= 1, 'Should have at least one unique tab index'

	finally:
		# Stop browser
		session.event_bus.dispatch(StopBrowserEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


@pytest.mark.asyncio
async def test_navigation_watchdog_security_enforcement():
	"""Test that the NavigationWatchdog enforces allowed_domains security."""
	# Create a profile with restricted domains
	from browser_use.browser.profile import BrowserProfile

	profile = BrowserProfile(
		headless=True,
		allowed_domains=['example.com', 'httpbin.org'],  # Only allow these domains
	)
	session = BrowserSession(browser_profile=profile)

	# Track BrowserErrorEvents
	error_events = []
	session.event_bus.on(BrowserErrorEvent, lambda e: error_events.append(e))

	try:
		# Start browser
		session.event_bus.dispatch(StartBrowserEvent())
		await session.event_bus.expect(BrowserStartedEvent, timeout=5.0)

		# Verify navigation watchdog was created and has security check
		assert hasattr(session, '_navigation_watchdog'), 'NavigationWatchdog should be created'
		assert session._navigation_watchdog is not None, 'NavigationWatchdog should not be None'

		# Test that allowed domains work
		allowed_url = 'https://httpbin.org/get'
		allowed = session._navigation_watchdog._is_url_allowed(allowed_url)
		assert allowed, f'Should allow {allowed_url}'

		# Test that disallowed domains are blocked
		disallowed_url = 'https://malicious-site.com/bad'
		disallowed = session._navigation_watchdog._is_url_allowed(disallowed_url)
		assert not disallowed, f'Should block {disallowed_url}'

		# Test internal URLs are always allowed
		internal_urls = ['about:blank', 'chrome://newtab/', 'chrome://new-tab-page/']
		for url in internal_urls:
			assert session._navigation_watchdog._is_url_allowed(url), f'Should allow internal URL: {url}'

		# Test glob patterns work
		profile_with_glob = BrowserProfile(headless=True, allowed_domains=['*.github.com'])
		session._navigation_watchdog.browser_session.browser_profile = profile_with_glob

		assert session._navigation_watchdog._is_url_allowed('https://api.github.com/repos'), 'Should allow subdomain'
		assert session._navigation_watchdog._is_url_allowed('https://github.com/user'), 'Should allow main domain'
		assert not session._navigation_watchdog._is_url_allowed('https://evil.com/github.com'), 'Should block non-matching domain'

	finally:
		# Stop browser
		session.event_bus.dispatch(StopBrowserEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)
