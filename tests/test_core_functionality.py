import os

import pytest
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.browser.browser import BrowserConfig
from browser_use.controller.service import Controller


@pytest.fixture
def llm():
	"""Initialize language model for testing"""

	# return ChatAnthropic(model_name='claude-3-5-sonnet-20240620', timeout=25, stop=None)
	return AzureChatOpenAI(
		model='gpt-4o',
		api_version='2024-10-21',
		azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT', ''),
		api_key=SecretStr(os.getenv('AZURE_OPENAI_KEY', '')),
	)


# pytest -s -k test_search_google
@pytest.fixture
async def controller():
	"""Initialize the controller with persistent browser"""
	controller = Controller(browser_config=BrowserConfig(keep_open=False, headless=False))

	try:
		yield controller
	finally:
		if controller.browser:
			await controller.browser.close(force=True)


@pytest.mark.asyncio
async def test_search_google(llm, controller):
	"""Test 'Search Google' action"""
	agent = Agent(
		task="Search Google for 'OpenAI'.",
		llm=llm,
		controller=controller,
	)
	history: AgentHistoryList = await agent.run(max_steps=2)
	action_names = history.action_names()
	assert 'search_google' in action_names


@pytest.mark.asyncio
async def test_go_to_url(llm, controller):
	"""Test 'Navigate to URL' action"""
	agent = Agent(
		task="Navigate to 'https://www.python.org'.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=2)
	action_names = history.action_names()
	assert 'go_to_url' in action_names


@pytest.mark.asyncio
async def test_go_back(llm, controller):
	"""Test 'Go back' action"""
	agent = Agent(
		task="Go to 'https://www.example.com', then go back.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	action_names = history.action_names()
	assert 'go_to_url' in action_names
	assert 'go_back' in action_names


@pytest.mark.asyncio
async def test_click_element(llm, controller):
	"""Test 'Click element' action"""
	agent = Agent(
		task="Go to 'https://www.python.org' and click on the first link.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=4)
	action_names = history.action_names()
	assert 'go_to_url' in action_names or 'open_tab' in action_names
	assert 'click_element' in action_names


@pytest.mark.asyncio
async def test_input_text(llm, controller):
	"""Test 'Input text' action"""
	agent = Agent(
		task="Go to 'https://www.google.com' and input 'OpenAI' into the search box.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=4)
	action_names = history.action_names()
	assert 'go_to_url' in action_names
	assert 'input_text' in action_names


@pytest.mark.asyncio
async def test_switch_tab(llm, controller):
	"""Test 'Switch tab' action"""
	agent = Agent(
		task="Open new tabs with 'https://www.google.com' and 'https://www.wikipedia.org', then switch to the first tab.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=6)
	action_names = history.action_names()
	open_tab_count = action_names.count('open_tab')
	assert open_tab_count >= 2
	assert 'switch_tab' in action_names


@pytest.mark.asyncio
async def test_open_new_tab(llm, controller):
	"""Test 'Open new tab' action"""
	agent = Agent(
		task="Open a new tab and go to 'https://www.example.com'.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	action_names = history.action_names()
	assert 'open_tab' in action_names


@pytest.mark.asyncio
async def test_extract_page_content(llm, controller):
	"""Test 'Extract page content' action"""
	agent = Agent(
		task="Go to 'https://www.example.com' and extract the page content.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	action_names = history.action_names()
	assert 'go_to_url' in action_names
	assert 'extract_content' in action_names


# pytest -k test_done_action
@pytest.mark.asyncio
async def test_done_action(llm, controller):
	"""Test 'Complete task' action"""
	agent = Agent(
		task="Navigate to 'https://www.example.com' and signal that the task is done.",
		llm=llm,
		controller=controller,
	)

	history = await agent.run(max_steps=3)
	action_names = history.action_names()
	assert 'go_to_url' in action_names
	assert 'done' in action_names


# run with: pytest -k test_scroll_down
@pytest.mark.asyncio
async def test_scroll_down(llm, controller: Controller):
	"""Test 'Scroll down' action and validate that the page actually scrolled"""
	agent = Agent(
		task="Go to 'https://en.wikipedia.org/wiki/Internet' and scroll down the page.",
		llm=llm,
		controller=controller,
	)
	# Get the browser instance
	browser = controller.browser
	page = await browser.get_current_page()

	# Navigate to the page and get initial scroll position
	await agent.run(max_steps=1)
	initial_scroll_position = await page.evaluate('window.scrollY;')

	# Perform the scroll down action
	await agent.run(max_steps=2)
	final_scroll_position = await page.evaluate('window.scrollY;')

	# Validate that the scroll position has changed
	assert final_scroll_position > initial_scroll_position, 'Page did not scroll down'

	# Validate that the 'scroll_down' action was executed
	history = agent.history
	action_names = history.action_names()
	assert 'scroll_down' in action_names
