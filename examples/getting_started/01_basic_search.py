import asyncio
import os
import sys

# Add the parent directory to the path so we can import browser_use
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))



from browser_use import Agent, ChatOpenAI
from browser_use.agent.config import AgentConfig


async def main():
	llm = ChatOpenAI(model='gpt-4.1-mini')
	task = "Search Google for 'what is browser automation' and tell me the top 3 results"
	config = AgentConfig(task=task, llm=llm)
	agent = Agent(config=config)
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
