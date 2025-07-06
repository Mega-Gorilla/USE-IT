import asyncio
import os
import sys

from browser_use.llm.openai.chat import ChatOpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

try:
	from lmnr import Laminar

	Laminar.initialize(project_api_key=os.getenv('LMNR_PROJECT_API_KEY'))
except Exception:
	print('Error initializing Laminar')
	pass

from browser_use import Agent

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4.1-mini',
)


task = 'Go to google.com/travel/flights and search for flights to Tokyo next week'
task = 'Directly call done and in the text write the description of the tool extract_structured_data'
agent = Agent(task=task, llm=llm)


async def main():
	history = await agent.run()
	# token usage
	print(history.usage)


if __name__ == '__main__':
	asyncio.run(main())
