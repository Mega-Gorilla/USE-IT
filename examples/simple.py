import asyncio
import os
import sys

from lmnr import Instruments

from browser_use.llm.openai.chat import ChatOpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

try:
	from lmnr import Laminar

	Laminar.initialize(
		project_api_key=os.getenv('LMNR_PROJECT_API_KEY'), disable_batch=True, disabled_instruments={Instruments.BROWSER_USE}
	)
except Exception:
	print('Error initializing Laminar')
	pass

from browser_use import Agent

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4.1-mini',
)


task = """Search for the translation of the word "love" from English to Spanish and list at least three contextual example sentences provided on Glosbe.
Only use https://glosbe.com/ to achieve the task. Don't go to any other site. The task is achievable with just navigation from this site."""
agent = Agent(task=task, llm=llm)


async def main():
	history = await agent.run()
	# token usage
	print(history.usage)


if __name__ == '__main__':
	asyncio.run(main())
