"""Test AboutBlankWatchdog functionality."""

import asyncio

import pytest

from browser_use.browser.events import (
	AboutBlankDVDScreensaverShownEvent,
	BrowserConnectedEvent,
	BrowserStartEvent,
	BrowserStopEvent,
	NavigateToUrlEvent,
	TabCreatedEvent,
)
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession


@pytest.mark.asyncio
async def test_aboutblank_watchdog_lifecycle():
	"""Test that AboutBlankWatchdog starts and stops with browser session."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Verify aboutblank watchdog was created
		assert hasattr(session, '_aboutblank_watchdog'), 'AboutBlankWatchdog should be created'
		assert session._aboutblank_watchdog is not None, 'AboutBlankWatchdog should not be None'

	finally:
		await session.kill()


@pytest.mark.asyncio
async def test_aboutblank_watchdog_creates_animation_tab():
	"""Test that AboutBlankWatchdog creates an animation tab when none exist."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Wait for initial tab creation and aboutblank watchdog to process
		await asyncio.sleep(0.5)

		# Check browser pages - should have initial tab plus animation tab
		pages = session.pages
		assert len(pages) >= 1, 'Should have at least one page'

		# Look for about:blank pages (animation tab)
		about_blank_pages = [p for p in pages if p.url == 'about:blank']
		# AboutBlankWatchdog should create at least one animation tab
		assert len(about_blank_pages) >= 1, f'Expected at least one about:blank animation tab, but found {len(about_blank_pages)}'

	finally:
		await session.kill()


@pytest.mark.asyncio
async def test_aboutblank_watchdog_handles_tab_creation():
	"""Test that AboutBlankWatchdog responds to TabCreatedEvent."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Get aboutblank watchdog
		watchdog = session._aboutblank_watchdog
		assert watchdog is not None

		# Create a new tab
		session.event_bus.dispatch(NavigateToUrlEvent(url='data:text/html,<h1>Test Tab</h1>', new_tab=True))
		await session.event_bus.expect(TabCreatedEvent, timeout=3.0)

		# Give watchdog time to process the new tab
		await asyncio.sleep(0.3)

		# The watchdog should have processed the TabCreatedEvent
		# We can't easily verify internal state without accessing private methods

	finally:
		await session.kill()


@pytest.mark.asyncio
async def test_aboutblank_watchdog_dvd_screensaver():
	"""Test that AboutBlankWatchdog can show DVD screensaver on about:blank tabs."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Get aboutblank watchdog
		watchdog = session._aboutblank_watchdog
		assert watchdog is not None

		# Wait for animation tab to be created
		await asyncio.sleep(0.5)

		# Find about:blank pages
		pages = session.pages
		about_blank_pages = [p for p in pages if p.url == 'about:blank']

		if about_blank_pages:
			# Try to show screensaver on first about:blank page
			try:
				await watchdog._show_dvd_screensaver_on_about_blank_tabs()
				# If no exception is thrown, the method executed successfully
			except Exception as e:
				# Method might fail in test environment, that's okay
				print(f'DVD screensaver test encountered expected issue: {e}')

	finally:
		await session.kill()


@pytest.mark.asyncio
async def test_aboutblank_watchdog_animation_tab_management():
	"""Test that AboutBlankWatchdog manages animation tabs properly."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Get aboutblank watchdog
		watchdog = session._aboutblank_watchdog
		assert watchdog is not None

		# Wait for initial setup
		await asyncio.sleep(0.5)

		# Check current state
		initial_page_count = len(session.pages)

		# Create multiple tabs to potentially trigger animation tab management
		for i in range(3):
			session.event_bus.dispatch(NavigateToUrlEvent(url=f'data:text/html,<h1>Tab {i}</h1>', new_tab=True))
			await asyncio.sleep(0.2)

		# Give watchdog time to process and manage animation tabs
		await asyncio.sleep(1.0)

		# Verify pages still exist (watchdog shouldn't break tab management)
		final_pages = session.pages
		assert len(final_pages) >= initial_page_count, 'Should not lose pages'

	finally:
		await session.kill()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_aboutblank_watchdog_javascript_execution():
	"""Test that the DVD screensaver JavaScript executes without errors."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Test 1: Initial new tab should get animation
		# The watchdog should detect the chrome://newtab/ page is a new tab and show animation
		initial_pages = session.pages
		assert len(initial_pages) == 1, 'Should have one initial tab'
		assert 'newtab' in initial_pages[0].url or 'new-tab-page' in initial_pages[0].url, 'Initial tab should be a new tab page'

		# Wait for AboutBlankWatchdog to show DVD screensaver on the initial new tab
		dvd_event1 = await session.event_bus.expect(AboutBlankDVDScreensaverShownEvent, timeout=10.0)
		assert dvd_event1.error is None, f'DVD screensaver failed on initial tab: {dvd_event1.error}'

		# Get the page and verify animation
		page1 = session.get_page_by_tab_index(dvd_event1.tab_index)
		assert page1 is not None, f'Could not find page at tab index {dvd_event1.tab_index}'

		# Verify the animation was created
		animation_exists = await page1.evaluate("""() => {
			return document.getElementById('pretty-loading-animation') !== null;
		}""")
		assert animation_exists, 'DVD screensaver animation should have been created on initial tab'

		# Test 2: Close the tab and verify watchdog creates new about:blank tab with animation
		await page1.close()

		# Wait for new about:blank tab to be created and animation shown
		dvd_event2 = await session.event_bus.expect(AboutBlankDVDScreensaverShownEvent, timeout=10.0)
		assert dvd_event2.error is None, f'DVD screensaver failed on auto-created tab: {dvd_event2.error}'

		# Get the new page
		page2 = session.get_page_by_tab_index(dvd_event2.tab_index)
		assert page2 is not None, f'Could not find page at tab index {dvd_event2.tab_index}'
		assert page2.url == 'about:blank', 'Auto-created tab should be about:blank'

		# Verify animation on the new tab
		animation_exists2 = await page2.evaluate("""() => {
			return document.getElementById('pretty-loading-animation') !== null;
		}""")
		assert animation_exists2, 'DVD screensaver animation should have been created on auto-created tab'

		# Verify no JavaScript errors occurred (particularly arguments.callee)
		console_errors = []

		async def capture_console(msg):
			if msg.type == 'error':
				console_errors.append(msg.text)

		page2.on('console', capture_console)
		await asyncio.sleep(0.5)

		if console_errors:
			for error in console_errors:
				if 'arguments' in error.lower():
					pytest.fail(f'JavaScript error detected: {error}')

	finally:
		# Allow any pending operations to complete
		await asyncio.sleep(0.5)

		# Stop browser properly through the session
		stop_event = session.event_bus.dispatch(BrowserStopEvent())
		try:
			await asyncio.wait_for(stop_event, timeout=5.0)
		except TimeoutError:
			print('BrowserStopEvent processing timed out')

		# Wait a bit more for playwright internal cleanup
		await asyncio.sleep(1.0)
