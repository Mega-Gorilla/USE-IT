import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from memory_profiler import profile

from browser_use import Agent

load_dotenv()

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4o',
	temperature=0.0,
)
task = 'First go to lego.com and find a nice lego set to buy. Once you have found a set, go to ceneje.si and find the cheapest offer available and return the link so i can buy it.'

agent = Agent(task=task, llm=llm)


@profile
async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
