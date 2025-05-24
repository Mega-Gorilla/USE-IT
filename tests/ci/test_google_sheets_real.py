"""
Real integration tests for Google Sheets actions against the actual Google Sheets website.
Tests the enhanced action registry system with Google Sheets keyboard automation.
Uses the existing Google Sheets actions from the main controller.
"""

import os

import pytest

from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.controller.service import Controller

# Test Google Sheets URL (public read-only spreadsheet for testing)
TEST_GOOGLE_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1INaIcfpYXlMRWO__de61SHFCaqt1lfHlcvtXZPItlpI/edit'


@pytest.fixture
async def browser_session():
	"""Create a real browser session for testing."""
	session = BrowserSession(
		browser_profile=BrowserProfile(
			executable_path=os.getenv('BROWSER_PATH'),
			user_data_dir=None,  # Use temporary profile
			headless=True,
		)
	)
	async with session:
		yield session


@pytest.fixture
def controller():
	"""Create a controller instance (Google Sheets actions are already registered)."""
	return Controller()


@pytest.mark.asyncio
async def test_selector_map_basic(browser_session, controller):
	"""Test that the selector map gets populated on a basic page."""
	# Go to a simple page first
	page = await browser_session.get_current_page()
	await page.goto('https://www.google.com')
	await page.wait_for_load_state()

	# Update browser state to populate selector map
	await browser_session.get_state_summary(cache_clickable_elements_hashes=False)

	# Check selector map
	selector_map = await browser_session.get_selector_map()
	print(f'Selector map size: {len(selector_map)}')

	# Should have some elements
	assert len(selector_map) > 0, 'No clickable elements found in selector map'


@pytest.mark.asyncio
async def test_click_element_basic(browser_session, controller):
	"""Test basic click element action to verify registry works."""
	# Go to a simple page
	page = await browser_session.get_current_page()
	await page.goto('https://www.google.com')
	await page.wait_for_load_state()

	# Update browser state to populate selector map
	await browser_session.get_state_summary(cache_clickable_elements_hashes=False)

	# Check selector map
	selector_map = await browser_session.get_selector_map()
	print(f'Available elements: {list(selector_map.keys())}')

	if len(selector_map) > 0:
		# Try to click the first available element
		first_index = list(selector_map.keys())[0]
		print(f'Trying to click element index: {first_index}')

		result = await controller.registry.execute_action(
			'click_element_by_index', {'index': first_index}, browser_session=browser_session
		)

		# Should not have an error about element not existing
		print(f'Click result: {result.extracted_content if result.extracted_content else "No content"}')
		print(f'Click error: {result.error if result.error else "No error"}')

		# The click might fail for other reasons (like navigation) but shouldn't fail due to "element does not exist"
		if result.error:
			assert 'Element with index' not in result.error, f'Element indexing failed: {result.error}'
	else:
		pytest.fail('No clickable elements found - DOM processing issue')


@pytest.mark.asyncio
async def test_google_sheets_open(browser_session, controller):
	"""Test opening a Google Sheet using the existing action."""
	# First check what actions are available
	available_actions = list(controller.registry.registry.actions.keys())
	print(f'Available actions: {[a for a in available_actions if "Google" in a]}')

	# Try to find the right action name
	google_sheet_actions = [a for a in available_actions if 'google sheet' in a.lower()]

	if not google_sheet_actions:
		pytest.skip('No Google Sheets actions found in controller')

	# Use the first Google Sheets action we find
	open_action = google_sheet_actions[0]
	print(f'Using action: {open_action}')

	result = await controller.registry.execute_action(
		open_action, {'google_sheet_url': TEST_GOOGLE_SHEET_URL}, browser_session=browser_session
	)

	print(f'Open result: {result.extracted_content if result.extracted_content else "No content"}')
	print(f'Open error: {result.error if result.error else "No error"}')

	# Verify we're on the Google Sheets page
	page = await browser_session.get_current_page()
	assert 'docs.google.com/spreadsheets' in page.url


@pytest.mark.asyncio
async def test_list_all_actions(browser_session, controller):
	"""Debug test to list all available actions."""
	available_actions = list(controller.registry.registry.actions.keys())
	print('All available actions:')
	for action in sorted(available_actions):
		print(f'  - {action}')

	# Just verify the controller has some actions
	assert len(available_actions) > 0
