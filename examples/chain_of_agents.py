import os
import sys

from langchain_openai import ChatOpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from browser_use import Agent, Browser, Controller


# Video: https://preview.screen.studio/share/8Elaq9sm
async def main():
	# Persist the browser state across agents

	browser = Browser()
	async with await browser.new_context() as context:
		model = ChatOpenAI(model='gpt-4o')

		# Initialize browser agent
		agent1 = Agent(
			task='Open 2 tabs with wikipedia articles about the history of the meta and one random wikipedia article.',
			llm=model,
			browser_context=context,
		)
		agent2 = Agent(
			task='Considering all open tabs give me the names of the wikipedia article.',
			llm=model,
			browser_context=context,
		)
		await agent1.run()
		await agent2.run()


asyncio.run(main())
