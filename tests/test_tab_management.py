import asyncio
import logging

import pytest
from dotenv import load_dotenv

load_dotenv()

from browser_use.agent.views import ActionModel
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext
from browser_use.controller.service import Controller

# Set up test logging
logger = logging.getLogger('tab_tests')
logger.setLevel(logging.DEBUG)


class TestTabManagement:
	"""Tests for the tab management system with separate agent_current_page and human_current_page references."""

	@pytest.fixture(scope='module')
	def event_loop(self):
		"""Create and provide an event loop for async tests."""
		loop = asyncio.get_event_loop_policy().new_event_loop()
		yield loop
		loop.close()

	@pytest.fixture(scope='module')
	async def browser(self, event_loop):
		"""Create and provide a Browser instance with security disabled."""
		browser_instance = Browser(
			config=BrowserConfig(
				headless=True,
				disable_security=True,
			)
		)
		yield browser_instance
		await browser_instance.close()

	@pytest.fixture
	async def browser_context(self, browser):
		"""Create and provide a BrowserContext instance with a properly initialized tab."""
		context = BrowserContext(browser=browser)

		# Initialize a session
		session = await context.get_session()

		# Ensure we start with no pages (close any that might exist)
		for page in session.context.pages:
			await page.close()

		# Create an initial tab and wait for it to load completely
		await context.create_new_tab('https://example.com/page1')
		await asyncio.sleep(1)  # Wait for the tab to fully initialize

		# Verify that agent_current_page and human_current_page are properly set
		assert context.agent_current_page is not None
		assert context.human_current_page is not None
		assert 'example.com' in context.agent_current_page.url

		yield context
		await context.close()

	@pytest.fixture
	def controller(self):
		"""Create and provide a Controller instance."""
		return Controller()

	# Helper methods

	async def _execute_action(self, controller, browser_context, action_data):
		"""Generic helper to execute any action via the controller."""
		# Dynamically create an appropriate ActionModel class
		action_type = list(action_data.keys())[0]
		action_value = action_data[action_type]

		# Create the ActionModel with the single action field
		class DynamicActionModel(ActionModel):
			pass

		# Dynamically add the field with the right type annotation
		setattr(DynamicActionModel, action_type, type(action_value) | None)

		# Execute the action
		result = await controller.act(DynamicActionModel(**action_data), browser_context)

		# Give the browser a moment to process the action
		await asyncio.sleep(0.5)

		return result

	async def _ensure_synchronized_state(self, browser_context):
		"""Helper to ensure tab references are properly synchronized before tests."""
		# Make sure agent_current_page and human_current_page are set and valid
		session = await browser_context.get_session()

		if not browser_context.agent_current_page or browser_context.agent_current_page not in session.context.pages:
			if session.context.pages:
				browser_context.agent_current_page = session.context.pages[0]
			else:
				# Create a tab with a real website
				await browser_context.create_new_tab('https://example.com/page1')
				await asyncio.sleep(1)  # Wait longer for tab to initialize

		if not browser_context.human_current_page or browser_context.human_current_page not in session.context.pages:
			browser_context.human_current_page = browser_context.agent_current_page

	async def _simulate_user_tab_change(self, page, browser_context):
		"""Simulate a user changing tabs by properly triggering events with Playwright."""
		logger.debug(
			f'BEFORE: agent_tab={browser_context.agent_current_page.url if browser_context.agent_current_page else "None"}, '
			f'human_current_page={browser_context.human_current_page.url if browser_context.human_current_page else "None"}'
		)
		logger.debug(f'Simulating user changing to -> {page.url}')

		# First bring the page to front - this is the physical action a user would take
		await page.bring_to_front()

		# To simulate a user switching tabs, we need to trigger the right events
		# Use Playwright's dispatch_event method to properly trigger events from outside

		await page.evaluate("""() => window.dispatchEvent(new Event('focus'))""")
		logger.debug('Dispatched window.focus event')

		# Give the event handlers time to process
		await asyncio.sleep(0.5)

		logger.debug(
			f'AFTER: agent_tab URL={browser_context.agent_current_page.url if browser_context.agent_current_page else "None"}, '
			f'human_current_page URL={browser_context.human_current_page.url if browser_context.human_current_page else "None"}'
		)

	# Tab management tests

	@pytest.mark.asyncio
	async def test_open_tab_updates_both_references(self, browser_context):
		"""Test that open_tab correctly updates both tab references."""
		# Ensure tab references are synchronized
		await self._ensure_synchronized_state(browser_context)

		# Store initial tab count and references
		session = await browser_context.get_session()
		initial_tab_count = len(session.context.pages)
		initial_agent_tab = browser_context.agent_current_page

		# Open a new tab directly via BrowserContext
		await browser_context.create_new_tab('https://example.com/page2')

		# Give time for events to process
		await asyncio.sleep(1)

		# Verify a new tab was created
		session = await browser_context.get_session()
		assert len(session.context.pages) == initial_tab_count + 1

		# Both references should be set to the new tab and different from initial tab
		assert browser_context.human_current_page is not None
		assert browser_context.agent_current_page is not None
		assert browser_context.human_current_page == browser_context.agent_current_page
		assert initial_agent_tab != browser_context.agent_current_page
		assert 'example.com/page2' in browser_context.agent_current_page.url

	@pytest.mark.asyncio
	async def test_switch_tab_updates_both_references(self, browser_context):
		"""Test that switch_tab updates both tab references."""
		# Ensure we start with at least one tab
		await self._ensure_synchronized_state(browser_context)

		# Create a new tab in addition to existing one
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(1)

		# Verify we now have the second tab active
		assert 'example.com/page2' in browser_context.agent_current_page.url

		# Switch to the first tab
		session = await browser_context.get_session()
		first_tab = session.context.pages[0]
		await browser_context.switch_to_tab(0)
		await asyncio.sleep(0.5)

		# Both references should point to the first tab
		assert browser_context.human_current_page is not None
		assert browser_context.agent_current_page is not None
		assert browser_context.human_current_page == browser_context.agent_current_page
		assert browser_context.agent_current_page == first_tab
		assert 'example.com/page1' in browser_context.agent_current_page.url

		# Verify the underlying page is correct by checking we can interact with it
		page = await browser_context.get_agent_current_page()
		title = await page.title()
		assert 'Example' in title

	@pytest.mark.asyncio
	async def test_close_tab_handles_references_correctly(self, browser_context):
		"""Test that closing a tab updates references correctly."""
		# Ensure we start with at least one tab
		await self._ensure_synchronized_state(browser_context)

		# Create two tabs with different URLs
		initial_tab = browser_context.agent_current_page
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(1)

		# Verify the second tab is now active
		assert 'example.com/page2' in browser_context.agent_current_page.url

		# Close the current tab
		await browser_context.close_current_tab()
		await asyncio.sleep(0.5)

		# Both references should be updated to the remaining available tab
		assert browser_context.human_current_page is not None
		assert browser_context.agent_current_page is not None
		assert browser_context.human_current_page == browser_context.agent_current_page
		assert browser_context.agent_current_page == initial_tab
		assert not browser_context.human_current_page.is_closed()
		assert 'example.com/page1' in browser_context.human_current_page.url

	@pytest.mark.asyncio
	async def test_user_changes_tab(self, browser_context):
		"""Test that agent_current_page is preserved when user changes the foreground tab."""
		# Ensure we start with at least one tab
		await self._ensure_synchronized_state(browser_context)

		# Create a second tab with a different URL
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(1)
		assert 'example.com/page2' in browser_context.agent_current_page.url

		# Switch back to the first tab for the agent
		session = await browser_context.get_session()
		first_tab = session.context.pages[0]
		await browser_context.switch_to_tab(0)
		await self._simulate_user_tab_change(first_tab, browser_context)
		await asyncio.sleep(0.5)

		# Store agent's active tab
		agent_tab = browser_context.agent_current_page
		assert 'example.com/page1' in agent_tab.url

		# Simulate user switching to the second tab
		session = await browser_context.get_session()
		user_tab = session.context.pages[1]  # Second tab

		# First, log the visibility listeners
		listeners = await user_tab.evaluate("() => Object.keys(window).filter(k => k.startsWith('onVisibilityChange'))")
		logger.debug(f'Tab visibility listeners: {listeners}')

		# Make sure handlers exist before attempting to trigger them
		assert len(listeners) > 0, 'No visibility listeners found on the page'

		# Now try the simulation
		await self._simulate_user_tab_change(user_tab, browser_context)

		# Verify agent_current_page remains unchanged while human_current_page changed
		assert browser_context.agent_current_page == agent_tab
		assert browser_context.human_current_page != browser_context.agent_current_page
		assert 'example.com/page1' in browser_context.agent_current_page.url
		assert 'example.com/page2' in browser_context.human_current_page.url

	@pytest.mark.asyncio
	async def test_get_agent_current_page(self, browser_context):
		"""Test that get_agent_current_page returns agent_current_page regardless of human_current_page."""
		# Ensure we start with at least one tab
		await self._ensure_synchronized_state(browser_context)

		# Create a second tab with a different URL
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(1)

		# Switch back to the first tab for the agent
		await browser_context.switch_to_tab(0)
		await asyncio.sleep(0.5)

		# Simulate user switching to the second tab
		session = await browser_context.get_session()
		user_tab = session.context.pages[1]  # Second tab
		await self._simulate_user_tab_change(user_tab, browser_context)

		# Verify get_agent_current_page returns agent's tab, not foreground tab
		agent_page = await browser_context.get_agent_current_page()
		assert agent_page == browser_context.agent_current_page
		assert agent_page != browser_context.human_current_page
		assert 'example.com/page1' in agent_page.url

		# Call a method on the page to verify it's fully functional
		title = await agent_page.title()
		assert 'Example' in title

	@pytest.mark.asyncio
	async def test_browser_operations_use_agent_current_page(self, browser_context):
		"""Test that browser operations use agent_current_page, not human_current_page."""
		# Ensure we start with at least one tab
		await self._ensure_synchronized_state(browser_context)

		# Create a second tab with a different URL
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(1)

		# Switch back to the first tab for the agent
		await browser_context.switch_to_tab(0)
		await asyncio.sleep(0.5)

		# Simulate user switching to the second tab
		session = await browser_context.get_session()
		user_tab = session.context.pages[1]  # Second tab
		await self._simulate_user_tab_change(user_tab, browser_context)

		# Verify we have the setup we want
		assert browser_context.human_current_page != browser_context.agent_current_page
		assert 'example.com/page2' in browser_context.human_current_page.url
		assert 'example.com/page1' in browser_context.agent_current_page.url

		# Execute a navigation directly on agent's tab
		agent_page = await browser_context.get_agent_current_page()
		await agent_page.goto('https://bing.com')
		await asyncio.sleep(0.5)

		# Verify navigation happened on agent_current_page
		assert 'bing.com' in browser_context.agent_current_page.url
		# But human_current_page remains unchanged
		assert 'example.com/page2' in browser_context.human_current_page.url

	@pytest.mark.asyncio
	async def test_tab_reference_recovery(self, browser_context):
		"""Test recovery when a tab reference becomes invalid."""
		# Ensure we start with at least one valid tab
		await self._ensure_synchronized_state(browser_context)

		# Create a second tab so we have multiple
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(1)

		# Deliberately corrupt the agent_current_page reference
		browser_context.agent_current_page = None

		# Call get_agent_current_page, which should recover the reference
		agent_page = await browser_context.get_agent_current_page()

		# Verify recovery worked
		assert agent_page is not None
		assert not agent_page.is_closed()

		# Verify the tab is fully functional
		title = await agent_page.title()
		assert title, 'Page should have a title'

		# Verify both references are now valid again
		assert browser_context.agent_current_page is not None
		assert browser_context.human_current_page is not None

	@pytest.mark.asyncio
	async def test_reconcile_tab_state_handles_both_invalid(self, browser_context):
		"""Test that reconcile_tab_state can recover when both tab references are invalid."""
		# Ensure we start with at least one valid tab
		await self._ensure_synchronized_state(browser_context)

		# Corrupt both references
		browser_context.agent_current_page = None
		browser_context.human_current_page = None

		# Call reconcile_tab_state directly
		await browser_context._reconcile_tab_state()

		# Verify both references are restored
		assert browser_context.agent_current_page is not None
		assert browser_context.human_current_page is not None
		# and they are the same tab
		assert browser_context.agent_current_page == browser_context.human_current_page
		# and the tab is valid
		assert not browser_context.agent_current_page.is_closed()

	@pytest.mark.asyncio
	async def test_race_condition_resilience(self, browser_context):
		"""Test resilience against race conditions in tab operations."""
		# Ensure we start with at least one valid tab
		await self._ensure_synchronized_state(browser_context)

		# Create two more tabs to have three in total
		await browser_context.create_new_tab('https://example.com/page2')
		await asyncio.sleep(0.5)
		await browser_context.create_new_tab('https://example.com/page3')
		await asyncio.sleep(0.5)

		# Verify we have at least 3 tabs
		session = await browser_context.get_session()
		assert len(session.context.pages) >= 3

		# Perform a series of rapid tab switches to simulate race conditions
		for i in range(5):
			tab_index = i % 3
			await browser_context.switch_to_tab(tab_index)
			await asyncio.sleep(0.1)  # Very short delay between switches

		# Verify the state is consistent after rapid operations
		assert browser_context.human_current_page is not None
		assert browser_context.agent_current_page is not None
		assert browser_context.human_current_page == browser_context.agent_current_page
		assert not browser_context.human_current_page.is_closed()

		# Verify we can still navigate on the final tab
		page = await browser_context.get_agent_current_page()
		await page.goto('https://example.com/page4')
		assert 'example.com/page4' in page.url
