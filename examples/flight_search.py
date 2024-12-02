"""
Simple try of the agent.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI

from browser_use import Agent, AgentHistoryList, Controller

llm = ChatOpenAI(model='gpt-4o')
# browser = Browser(config=BrowserConfig(headless=False))
controller = Controller()


@controller.registry.action(description='Prints the secret text')
async def secret_text(secret_text: str) -> None:
	print(secret_text)


agent = Agent(
	task='Find flights on kayak.com from Zurich to Beijing on 25.12.2024 to 02.02.2025',
	llm=llm,
	controller=controller,
	# browser=browser,
)


async def main():
	history: AgentHistoryList = await agent.run(2)
	# await controller.browser.close(force=True)

	# await browser.close()


asyncio.run(main())
