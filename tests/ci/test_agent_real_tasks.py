import glob
import os

import aiofiles
import pytest
import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

# Directory containing contributed tasks
task_dir = os.path.join(os.path.dirname(__file__), '../agent_tasks')
task_files = glob.glob(os.path.join(task_dir, '*.yaml'))


class JudgeResponse(BaseModel):
	success: bool
	explanation: str


@pytest.mark.asyncio
@pytest.mark.parametrize('task_file', task_files)
async def test_agent_real_task(task_file):
	async with aiofiles.open(task_file, 'r') as f:
		content = await f.read()
	task_data = yaml.safe_load(content)
	task = task_data['task']
	judge_context = task_data.get('judge_context', ['The agent must solve the task'])
	max_steps = task_data.get('max_steps', 15)
	agent_llm = ChatOpenAI(model='gpt-4o-mini')
	judge_llm = ChatOpenAI(model='gpt-4o-mini')

	shared_profile = BrowserProfile(
		headless=True,
		user_data_dir=None,  # use dedicated tmp user_data_dir per session
		keep_alive=True,  # don't close the browser after the agent finishes (only needed to save the session's updated cookies to disk after the run, see below)
	)
	session = BrowserSession(browser_profile=shared_profile)
	agent = Agent(task=task, llm=agent_llm, browser_session=session)
	history: AgentHistoryList = await agent.run(max_steps=max_steps)
	agent_output = history.final_result() or ''
	assert agent_output, 'Agent did not return any output'
	judge_prompt = f"""
You are a evaluator of a browser agent task inside a ci/cd pipeline. Here was the agent's task:
{task}

Here is the agent's output:
{agent_output}

Criteria for success:
- {chr(10).join(judge_context)}

Reply in JSON with keys: success (true/false), explanation (string).
"""
	structured_llm = judge_llm.with_structured_output(JudgeResponse)
	judge_response = await structured_llm.ainvoke(judge_prompt)
	assert judge_response.success, f'Judge failed: {judge_response.explanation}'
