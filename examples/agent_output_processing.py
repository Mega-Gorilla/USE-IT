import os
import sys
from pprint import pprint

from browser_use.controller.service import Controller

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from langchain_openai import ChatOpenAI

from browser_use import Agent, BrowserConfig
from browser_use.agent.views import AgentHistoryList

llm = ChatOpenAI(model='gpt-4o')

agent = Agent(
	task="go to google.com and type 'OpenAI' click search and give me the first url",
	llm=llm,
	controller=Controller(browser_config=BrowserConfig(keep_open=True, headless=False)),
)


async def main():
	history: AgentHistoryList = await agent.run()

	print('Final Result:')
	pprint(history.final_result(), indent=4)

	print('\nErrors:')
	pprint(history.errors(), indent=4)

	# e.g. xPaths the model clicked on
	print('\nModel Outputs:')
	pprint(history.model_actions(), indent=4)

	print('\nThoughts:')
	pprint(history.model_thoughts(), indent=4)


if __name__ == '__main__':
	asyncio.run(main())
