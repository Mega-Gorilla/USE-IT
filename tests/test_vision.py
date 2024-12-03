"""
Simple try of the agent.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import json
import os
import sys
from doctest import OutputChecker
from pprint import pprint

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI

from browser_use import ActionModel, Agent, AgentHistoryList, Controller
from browser_use.agent.views import AgentOutput

llm = ChatOpenAI(model='gpt-4o')
controller = Controller()

# use this test to ask the model questions about the page like
# which color do you see for bbox labels, list all with their label
# whats the smallest bboxes with labels


@controller.registry.action(description='explain what you see on the screen and ask user for input')
async def explain_screen(text: str) -> str:
	pprint(text)
	answer = input('\nuser input next question: \n')
	return answer


agent = Agent(
	task='call explain_screen all the time the user asks you questions e.g. about the page like bbox which you see are labels  - your task is to expalin it and get the next question',
	llm=llm,
	controller=controller,
)


async def main():
	history: AgentHistoryList = await agent.run(20)
	await controller.browser.close(force=True)


asyncio.run(main())
