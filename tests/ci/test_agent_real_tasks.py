import glob
import os

import aiofiles
import pytest
import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList

# Directory containing contributed tasks
task_dir = os.path.join(os.path.dirname(__file__), '../agent_tasks')
task_files = glob.glob(os.path.join(task_dir, '*.yaml'))


class JudgeResponse(BaseModel):
	success: bool
	explanation: str


@pytest.mark.parametrize('task_file', task_files)
@pytest.mark.asyncio
async def test_agent_real_task(task_file):
	async with aiofiles.open(task_file, 'r') as f:
		content = await f.read()
	task_data = yaml.safe_load(content)

	task = task_data['task']
	judge_context = task_data.get('judge_context', [])
	if not judge_context:
		judge_context = ['The agent must solve the task']

	# Use Llama 4 (Groq) for the agent
	# agent_llm = ChatOpenAI(
	# 	model='meta-llama/llama-4-maverick-17b-128e-instruct',
	# 	base_url='https://api.groq.com/openai/v1',
	# 	api_key=os.environ.get('GROQ_API_KEY'),
	# 	temperature=0.0,
	# )
	agent_llm = ChatOpenAI(model='gpt-4o-mini')

	# Use gpt-4o-mini as the judge
	judge_llm = ChatOpenAI(model='gpt-4o-mini')

	# Run the agent
	agent = Agent(task=task, llm=agent_llm)
	history: AgentHistoryList = await agent.run(max_steps=20)
	agent_output = history.final_result() or ''
	if not agent_output:
		pytest.fail('Agent did not return any output')

	# Compose judge prompt
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
	assert judge_response.success is True, f'Judge failed the agent: {judge_response.explanation}'
