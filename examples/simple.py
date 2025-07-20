import asyncio
import os
import sys

from browser_use.llm.openai.chat import ChatOpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

try:
	from lmnr import Instruments, Laminar

	Laminar.initialize(project_api_key=os.getenv('LMNR_PROJECT_API_KEY'), disabled_instruments={Instruments.BROWSER_USE})
except Exception:
	print('Error initializing Laminar')
	pass

from browser_use import Agent

# Initialize the model
llm = ChatOpenAI(
	model='o4-mini',
	temperature=0.2,
)

# Optimized task with more specific instructions
task = """
Navigate to the Qatar Airways homepage and search for flights from Doha to Paris departing in the upcoming week; then list the available fare classes and prices.
Only use https://www.qatarairways.com/ to achieve the task. Don't go to any other site. The task is achievable with just navigation from this site."""

task = ' call done directly and repeat the word "New" 100 times'
# Performance optimizations
agent = Agent(
	task=task,
	llm=llm,
)


async def main():
	history = await agent.run(max_steps=10)
	# token usage
	print(history.usage)


if __name__ == '__main__':
	asyncio.run(main())
