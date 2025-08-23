"""
Simple demonstration of the CDP feature.

To test this locally, follow these steps:
1. Create a shortcut for the executable Chrome file.
2. Add the following argument to the shortcut:
   - On Windows: `--remote-debugging-port=9222`
3. Open a web browser and navigate to `http://localhost:9222/json/version` to verify that the Remote Debugging Protocol (CDP) is running.
4. Launch this example.

@dev You need to set the `OPENAI_API_KEY` environment variable before proceeding.
"""

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from browser_use import Agent, Controller
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.llm import ChatOpenAI

api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
	raise ValueError('OPENAI_API_KEY is not set')

browser_session = BrowserSession(
	browser_profile=BrowserProfile(
		headless=False,
	),
	cdp_url='http://localhost:9222',
	is_local=True,  # set to False if you want to use a remote browser
)
controller = Controller()


async def main():
	task = 'Go to "https://v0-download-and-upload-text.vercel.app/" download the text file, and upload it to the website.'
	# Assert api_key is not None to satisfy type checker
	assert api_key is not None, 'OPENAI_API_KEY must be set'
	model = ChatOpenAI(model='gpt-4.1-mini', api_key=api_key)
	agent = Agent(
		task=task,
		llm=model,
		controller=controller,
		browser_session=browser_session,
	)

	await agent.run()
	await browser_session.kill()

	input('Press Enter to close...')


if __name__ == '__main__':
	asyncio.run(main())
