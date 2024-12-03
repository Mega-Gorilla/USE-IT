"""
Simple try of the agent.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import os
import sys

from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI

from browser_use import Agent, AgentHistoryList, Controller

llm = ChatOpenAI(model='gpt-4o')
# browser = Browser(config=BrowserConfig(headless=False))

agent = Agent(
	task=(
		'Find flights on kayak.com from  Zurich to San Francisco on 25.12.2024 to 02.02.2025, make sure to select the right month '
	),
	llm=llm,
	validate_output=True,
	browser_context=BrowserContext(
		browser=Browser(config=BrowserConfig(headless=False)),
	),
)


async def main():
	history: AgentHistoryList = await agent.run(20)
	# await controller.browser.close(force=True)
	input('Press Enter to close the browser')
	# await browser.close()


asyncio.run(main())
