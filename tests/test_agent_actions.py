import os

import pytest
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, SecretStr

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.browser.service import BrowserConfig
from browser_use.browser.views import BrowserState
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
	# return ChatOpenAI(model='gpt-4o-mini')


@pytest.fixture
async def agent_with_controller():
	"""Create agent with controller for testing"""
	controller = Controller(browser_config=BrowserConfig(keep_open=False))
	print('init controller')
	try:
		yield controller
	finally:
		if controller.browser:
			await controller.browser.close(force=True)


# pytest tests/test_agent_actions.py -v -k "test_ecommerce_interaction" --capture=no
# @pytest.mark.asyncio
async def test_ecommerce_interaction(llm, agent_with_controller):
	"""Test complex ecommerce interaction sequence"""
	agent = Agent(
		task="Go to amazon.com, search for 'laptop', filter by 4+ stars, and find the price of the first result",
		llm=llm,
		controller=agent_with_controller,
		save_conversation_path='tmp/test_ecommerce_interaction/conversation',
	)

	history: AgentHistoryList = await agent.run(max_steps=20)

	# Verify sequence of actions
	action_sequence = []
	for action in history.model_actions():
		action_name = list(action.keys())[0]
		if action_name in ['go_to_url', 'open_tab']:
			action_sequence.append('navigate')
		elif action_name == 'input_text':
			action_sequence.append('input')
			# Check that the input is 'laptop'
			inp = action['input_text']['text'].lower()  # type: ignore
			if inp == 'laptop':
				action_sequence.append('input_exact_correct')
			elif 'laptop' in inp:
				action_sequence.append('correct_in_input')
			else:
				action_sequence.append('incorrect_input')
		elif action_name == 'click_element':
			action_sequence.append('click')

	# Verify essential steps were performed
	assert 'navigate' in action_sequence  # Navigated to Amazon
	assert 'input' in action_sequence  # Entered search term
	assert 'click' in action_sequence  # Clicked search/filter
	assert 'input_exact_correct' in action_sequence or 'correct_in_input' in action_sequence


# @pytest.mark.asyncio
async def test_error_recovery(llm, agent_with_controller):
	"""Test agent's ability to recover from errors"""
	agent = Agent(
		task='Navigate to nonexistent-site.com and then recover by going to google.com ',
		llm=llm,
		controller=agent_with_controller,
	)

	history: AgentHistoryList = await agent.run(max_steps=10)

	actions_names = history.action_names()
	actions = history.model_actions()
	assert (
		'go_to_url' in actions_names or 'open_tab' in actions_names
	), f'{actions_names} does not contain go_to_url or open_tab'
	for action in actions:
		if 'go_to_url' in action:
			assert 'url' in action['go_to_url'], 'url is not in go_to_url'
			assert action['go_to_url']['url'].endswith(
				'google.com'
			), 'url does not end with google.com'
			break


# @pytest.mark.asyncio
async def test_find_contact_email(llm, agent_with_controller):
	"""Test agent's ability to find contact email on a website"""
	agent = Agent(
		task='Go to https://browser-use.com/ and find out the contact email',
		llm=llm,
		controller=agent_with_controller,
	)

	history: AgentHistoryList = await agent.run(max_steps=10)

	# Verify the agent found the contact email
	extracted_content = history.extracted_content()
	email = 'info@browser-use.com'
	for content in extracted_content:
		if email in content:
			break
	else:
		pytest.fail(f'{extracted_content} does not contain {email}')


# @pytest.mark.asyncio
async def test_agent_finds_installation_command(llm, agent_with_controller):
	"""Test agent's ability to find the pip installation command for browser-use on the web"""
	agent = Agent(
		task='Find the pip installation command for the browser-use repo',
		llm=llm,
		controller=agent_with_controller,
	)

	history: AgentHistoryList = await agent.run(max_steps=10)

	# Verify the agent found the correct installation command
	extracted_content = history.extracted_content()
	install_command = 'pip install browser-use'
	for content in extracted_content:
		if install_command in content:
			break
	else:
		pytest.fail(f'{extracted_content} does not contain {install_command}')


class CaptchaTest(BaseModel):
	name: str
	url: str
	success_text: str
	additional_text: str | None = None


# run 3 test: python -m pytest tests/test_agent_actions.py -v -k "test_captcha_solver" --capture=no --log-cli-level=INFO
# pytest tests/test_agent_actions.py -v -k "test_captcha_solver" --capture=no --log-cli-level=INFO
@pytest.mark.asyncio
@pytest.mark.parametrize(
	'captcha',
	[
		# good test for num_clicks
		CaptchaTest(
			name='Text Captcha',
			url='https://2captcha.com/demo/text',
			success_text='Captcha is passed successfully!',
		),
		CaptchaTest(
			name='Basic Captcha',
			url='https://captcha.com/demos/features/captcha-demo.aspx',
			success_text='Correct!',
		),
		CaptchaTest(
			name='Rotate Captcha',
			url='https://2captcha.com/demo/rotatecaptcha',
			success_text='Captcha is passed successfully',
			additional_text='Use num_clicks with number to click multiple times at once in same direction. click done when image is exact correct position.',
		),
		CaptchaTest(
			name='MT Captcha',
			url='https://2captcha.com/demo/mtcaptcha',
			success_text='Verified Successfully',
			additional_text='Stop when you solved it successfully.',
		),
	],
)
async def test_captcha_solver(llm, agent_with_controller: Controller, captcha: CaptchaTest):
	"""Test agent's ability to solve different types of captchas"""
	agent = Agent(
		task=f'Go to {captcha.url} and solve the captcha. {captcha.additional_text}',
		llm=llm,
		controller=agent_with_controller,
	)
	from browser_use.agent.views import AgentHistoryList

	history: AgentHistoryList = await agent.run(max_steps=7)

	state: BrowserState = await agent_with_controller.browser.get_state()

	all_text = state.element_tree.get_all_text_till_next_clickable_element()

	if not all_text:
		all_text = ''

	if not isinstance(all_text, str):
		all_text = str(all_text)

	solved = captcha.success_text in all_text
	assert solved, f'Failed to solve {captcha.name}'


# python -m pytest tests/test_agent_actions.py -v --capture=no


# pytest tests/test_agent_actions.py -v -k "test_captcha_solver" --capture=no --log-cli-level=INFO
