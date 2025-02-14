import asyncio
import json
import logging
from typing import Any

import inngest
import inngest.fast_api
from fastapi import FastAPI
from langchain_openai import ChatOpenAI

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentState
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext


class InMemoryDatabase:
	def __init__(self):
		self.data: dict[str, str] = {}
		self.key_counter = 0

	async def get(self, key: str) -> Any:
		raw = self.data.get(key)
		if raw is None:
			return None
		return json.loads(raw)

	async def set(self, value: Any):
		key = f'state_{self.key_counter}'
		self.data[key] = json.dumps(value)
		self.key_counter += 1
		return key


database_service = InMemoryDatabase()


class InMemoryBrowserService:
	def __init__(self):
		self.browser = Browser(
			config=BrowserConfig(
				headless=False,
			)
		)
		self.contexts: dict[int, BrowserContext] = {}
		self.id_counter = 0

	async def create_context(self):
		# Simulate a remote browser creation.
		await asyncio.sleep(1)

		context = await self.browser.new_context()

		id = self.id_counter
		self.contexts[id] = context
		self.id_counter += 1

		return id

	async def get_context(self, id: int):
		# Simulate a remote browser fetching.
		return self.browser, self.contexts[id]


browser_service = InMemoryBrowserService()


# Inngest Loop

inngest_client = inngest.Inngest(
	app_id='browser-use-inngest',
	logger=logging.getLogger('uvicorn'),
)

# INNGEST_DEV=1 uvicorn main:app --reload
# npx inngest-cli@latest dev -u http://127.0.0.1:8000/api/inngest --no-discovery


async def create_agent(task: str) -> tuple[str, str, int]:
	context_id = await browser_service.create_context()

	initial_state = AgentState().model_dump_json(exclude={'history'})
	state_id = await database_service.set(initial_state)
	task_id = await database_service.set(task)

	return task_id, state_id, context_id


async def agent_step(task_id: str, state_id: str, browser_id: int) -> tuple[bool, bool, str]:
	browser, browser_context = await browser_service.get_context(browser_id)
	state = AgentState.model_validate_json(await database_service.get(state_id))
	task = await database_service.get(task_id)

	agent = Agent(
		task=task,
		llm=ChatOpenAI(model='gpt-4o'),
		browser=browser,
		browser_context=browser_context,
		injected_agent_state=state,
		page_extraction_llm=ChatOpenAI(model='gpt-4o-mini'),
	)

	done, valid = await agent.take_step()

	new_state = state.model_dump_json(exclude={'history'})
	new_state_id = await database_service.set(new_state)

	return done, valid, new_state_id


# Create an Inngest function
@inngest_client.create_function(
	fn_id='run-agent',
	# Event that triggers this function
	trigger=inngest.TriggerEvent(event='app/run-agent'),
)
async def run_agent(ctx: inngest.Context, step: inngest.Step) -> str:
	ctx.logger.info('Running agent')

	task: str = ctx.event.data['task']  # type: ignore

	# Init
	task_id, state_id, context_id = await step.run('create_agent', create_agent, task)

	last_state_id = state_id

	# Loop
	for i in range(10):
		done, valid, state_id = await step.run('agent_step', agent_step, task_id, last_state_id, context_id)
		last_state_id = state_id

		# Save state to file
		if done and valid:
			break

	return await database_service.get(last_state_id)


app = FastAPI()

# Serve the Inngest endpoint
inngest.fast_api.serve(app, inngest_client, [run_agent])
