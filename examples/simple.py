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
task = 'Go to apple.com and find the latest iphone model and return the link so i can buy it.'

agent = Agent(task=task, llm=llm)


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
