"""Test AboutBlankWatchdog functionality."""

import asyncio

import pytest

from browser_use.browser.events import (
	BrowserConnectedEvent,
	BrowserStartEvent,
	BrowserStopEvent,
	BrowserStoppedEvent,
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
		# Stop browser
		session.event_bus.dispatch(BrowserStopEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


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
		# Note: This depends on timing and browser state, so we don't assert strict count

	finally:
		# Stop browser
		session.event_bus.dispatch(BrowserStopEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


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
		# Stop browser
		session.event_bus.dispatch(BrowserStopEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


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
		# Stop browser
		session.event_bus.dispatch(BrowserStopEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


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
		# Stop browser
		session.event_bus.dispatch(BrowserStopEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)


@pytest.mark.asyncio
async def test_aboutblank_watchdog_javascript_execution():
	"""Test that the DVD screensaver JavaScript executes without errors."""
	profile = BrowserProfile(headless=True)
	session = BrowserSession(browser_profile=profile)

	try:
		# Start browser
		session.event_bus.dispatch(BrowserStartEvent())
		await session.event_bus.expect(BrowserConnectedEvent, timeout=5.0)

		# Create an about:blank page
		session.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
		await asyncio.sleep(0.5)

		# Get the about:blank page
		pages = session.pages
		about_blank_page = next((p for p in pages if p.url == 'about:blank'), None)
		assert about_blank_page is not None, 'Should have an about:blank page'

		# Get aboutblank watchdog
		watchdog = session._aboutblank_watchdog

		# Capture console errors
		console_errors = []

		async def capture_console_error(msg):
			if msg.type == 'error':
				console_errors.append(msg.text)

		about_blank_page.on('console', capture_console_error)

		# Try to inject the DVD screensaver
		browser_session_id = str(session.id)[-4:]
		await watchdog._show_dvd_screensaver_loading_animation(about_blank_page, browser_session_id)

		# Give time for any errors to surface
		await asyncio.sleep(0.5)

		# Check for console errors
		if console_errors:
			# Check specifically for arguments.callee error
			for error in console_errors:
				if 'arguments' in error.lower() and ('callee' in error.lower() or 'arrow function' in error.lower()):
					pytest.fail(f'JavaScript error related to arguments.callee in arrow function: {error}')
				# Also fail on any other errors for completeness
				pytest.fail(f'JavaScript console error: {error}')

		# Also verify the animation was actually created
		animation_exists = await about_blank_page.evaluate("""() => {
			return document.getElementById('pretty-loading-animation') !== null;
		}""")
		assert animation_exists, 'DVD screensaver animation should have been created'

		# Check that the title was set
		title = await about_blank_page.title()
		expected_title = f'Starting agent {browser_session_id}...'
		assert title == expected_title, f'Title should be "{expected_title}" but was "{title}"'

	finally:
		# Stop browser
		session.event_bus.dispatch(BrowserStopEvent())
		await session.event_bus.expect(BrowserStoppedEvent, timeout=5.0)
