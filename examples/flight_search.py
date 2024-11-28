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
controller = Controller(keep_open=False, cookies_path='cookies.json')


@controller.registry.action(description='Prints the secret text')
async def secret_text(secret_text: str) -> None:
	print(secret_text)


agent = Agent(
	# task='Go to kayak.com and search for flights from Zurich to Beijing and then done.',
	task='Find flights on kayak.com from Zurich to Beijing on 25.12.2024 to 02.02.2025',
	# task='Search for elon musk on google and click the first result scroll down',
	llm=llm,
	controller=controller,
)


async def main():
	# history: AgentHistoryList = await agent.run(5)
	# await controller.browser.close(force=True)

	history_file_path = 'AgentHistoryList.json'
	# agent.save_history(file_path=history_file_path)

	agent2 = Agent(llm=llm, controller=controller, task='')
	await agent2.load_and_rerun(history_file_path)

	await controller.browser.close(force=True)


asyncio.run(main())
