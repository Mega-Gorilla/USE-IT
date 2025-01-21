from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent

load_dotenv()
llm = ChatOpenAI(model='gpt-4o')

initial_actions = [
	{'open_tab': {'url': 'https://www.amazon.com'}},
	{'scroll_down': {'amount': 1000}},
	{'scroll_down': {'amount': 1000}},
	{'click_element': {'index': 5}},
	{'click_element': {'index': 1}},
	{'open_tab': {'url': 'https://www.google.com'}},
	{'click_element': {'index': 1}},
]

agent = Agent(task='Your task description', llm=llm, initial_actions=initial_actions)


async def main():
	await agent.run(max_steps=10)


if __name__ == '__main__':
	import asyncio

	asyncio.run(main())
