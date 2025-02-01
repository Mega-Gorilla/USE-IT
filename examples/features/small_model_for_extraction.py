import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent
from browser_use.controller.service import Controller

load_dotenv()

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4o',
	temperature=0.0,
)
task = 'Find the founders of browser-use in ycombinator, extract all links and open the links one by one'


# smaller model to extract content
small_llm = ChatOpenAI(model='gpt-4o-mini', temperature=0.0)

agent = Agent(task=task, llm=llm, max_actions_per_step=2, page_extraction_llm=small_llm)


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
