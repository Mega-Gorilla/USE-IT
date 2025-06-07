"""
Runs all agent tasks in parallel (up to 10 at a time) and prints a big score summary.
Does not fail on partial failures (always exits 0).
"""

import asyncio
import glob
import os
import sys

import aiofiles
import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

# --- CONFIG ---
MAX_PARALLEL = 10
TASK_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '../agent_tasks')
TASK_FILES = glob.glob(os.path.join(TASK_DIR, '*.yaml'))


class JudgeResponse(BaseModel):
	success: bool
	explanation: str


async def run_task(task_file, semaphore):
	async with semaphore:
		async with aiofiles.open(task_file, 'r') as f:
			content = await f.read()
		task_data = yaml.safe_load(content)
		task = task_data['task']
		judge_context = task_data.get('judge_context', ['The agent must solve the task'])
		max_steps = task_data.get('max_steps', 15)
		agent_llm = ChatOpenAI(model='gpt-4.1-mini')
		judge_llm = ChatOpenAI(model='gpt-4.1-mini')

		shared_profile = BrowserProfile(
			headless=True,
			user_data_dir=None,  # use dedicated tmp user_data_dir per session
			args=['--no-sandbox'],
		)
		session = BrowserSession(browser_profile=shared_profile)
		agent = Agent(task=task, llm=agent_llm, browser_session=session)
		history: AgentHistoryList = await agent.run(max_steps=max_steps)
		agent_output = history.final_result() or ''
		criteria = chr(10) + '- '.join(judge_context)
		judge_prompt = f"""
You are a evaluator of a browser agent task inside a ci/cd pipeline. Here was the agent's task:
{task}

Here is the agent's output:
{agent_output}

Criteria for success:
- {criteria}

Reply in JSON with keys: success (true/false), explanation (string).
"""

		structured_llm = judge_llm.with_structured_output(JudgeResponse)
		judge_response = await structured_llm.ainvoke(judge_prompt)
		return {'file': os.path.basename(task_file), 'success': judge_response.success, 'explanation': judge_response.explanation}


async def main():
	semaphore = asyncio.Semaphore(MAX_PARALLEL)
	print(TASK_FILES)
	tasks = [run_task(task_file, semaphore) for task_file in TASK_FILES]
	results = await asyncio.gather(*tasks)

	passed = sum(1 for r in results if r['success'])
	total = len(results)

	print('\n' + '=' * 60)
	print(f'{"RESULTS":^60}\n')

	# Prepare table data
	headers = ['Task', 'Success', 'Reason']
	rows = []
	for r in results:
		status = '✅' if r['success'] else '❌'
		rows.append([r['file'], status, r['explanation']])

	# Calculate column widths
	col_widths = [max(len(str(row[i])) for row in ([headers] + rows)) for i in range(3)]

	# Print header
	header_row = ' | '.join(headers[i].ljust(col_widths[i]) for i in range(3))
	print(header_row)
	print('-+-'.join('-' * w for w in col_widths))

	# Print rows
	for row in rows:
		print(' | '.join(str(row[i]).ljust(col_widths[i]) for i in range(3)))

	print('\n' + '=' * 60)
	print(f'\n{"SCORE":^60}')
	print(f'\n{"=" * 60}\n')
	print(f'\n{"*" * 10}  {passed}/{total} PASSED  {"*" * 10}\n')
	print('=' * 60 + '\n')

	return passed, total


if __name__ == '__main__':
	passed, total = asyncio.run(main())
	print(f'PASSED={passed}')
	print(f'TOTAL={total}')
