import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent

load_dotenv()

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4o',
	temperature=0.0,
)
planner_llm = ChatOpenAI(
	model='o3-mini',
)
task = 'find all information you find about the browser-use founders (minimum 10 links) and write a short message to them'

agent = Agent(task=task, llm=llm, planner_llm=planner_llm, use_vision_for_planner=False)


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
