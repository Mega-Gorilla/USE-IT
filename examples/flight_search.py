"""
Simple try of the agent.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import json
import os
import sys
from doctest import OutputChecker
from pprint import pprint

from browser_use.agent.views import AgentBrain
from browser_use.dom.history_tree_processor import HistoryTreeProcessor
from browser_use.dom.views import DOMElementNode, SelectorMap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI

from browser_use import ActionModel, Agent, AgentHistoryList, Controller
from browser_use.agent.views import AgentOutput

llm = ChatOpenAI(model='gpt-4o')
controller = Controller()


@controller.registry.action(description='Prints the secret text')
async def secret_text(secret_text: str) -> None:
	print(secret_text)


agent = Agent(
	task='Find flights on kayak.com from Zurich to Beijing on 25.12.2024 to 02.02.2025',
	llm=llm,
	controller=controller,
)


async def main():
	history: AgentHistoryList = await agent.run(20)
	await controller.browser.close(force=True)


asyncio.run(main())
