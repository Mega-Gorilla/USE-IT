import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent, Controller

load_dotenv()


async def run_agent(task: str):
	llm = ChatOpenAI(
		model='gpt-4o',
		temperature=0.0,
	)
	agent = Agent(
		task=task,
		llm=llm,
		include_attributes=[
			'title',
			'type',
			'name',
			'role',
			'tabindex',
			'aria-label',
			'placeholder',
			'value',
			'alt',
			'aria-expanded',
			'href',
		],
	)

	history = await agent.run()
	final_response = history.final_result()
	return final_response, history.history
