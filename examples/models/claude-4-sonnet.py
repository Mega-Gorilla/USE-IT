"""
Simple script that runs the task of opening amazon and searching.
Ensure `ANTHROPIC_API_KEY` is provided via config.yaml or the environment.
"""

import asyncio
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from browser_use import Agent
from browser_use.llm import ChatAnthropic

llm = ChatAnthropic(model='claude-sonnet-4-0', temperature=0.0)

agent = Agent(
	task='Go to amazon.com, search for laptop, sort by best rating, and give me the price of the first result',
	llm=llm,
)

async def main():
	await agent.run(max_steps=10)

asyncio.run(main())
