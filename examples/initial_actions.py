from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent

load_dotenv()
llm = ChatOpenAI(model='gpt-4o')

initial_actions = [
	{'open_tab': {'url': 'https://www.google.com'}},
	{'input_text': {'index': 5, 'text': 'Whats the next hot AI company?'}},
	{'send_keys': {'keys': 'Enter'}},
	{'extract_content': {'include_links': True}},
]
agent = Agent(
	task='Go to each company and quickly summarize what they do.',
	initial_actions=initial_actions,
	llm=llm,
)


async def main():
	await agent.run(max_steps=10)


if __name__ == '__main__':
	import asyncio

	asyncio.run(main())
