"""
Example of how it supports cross-origin iframes.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent, Controller
from browser_use.browser.browser import Browser, BrowserConfig

# Load environment variables
load_dotenv()
if not os.getenv('OPENAI_API_KEY'):
	raise ValueError('OPENAI_API_KEY is not set. Please add it to your environment variables.')


browser = Browser(
	config=BrowserConfig(
		browser_instance_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
	)
)
controller = Controller()


async def main():
	agent = Agent(
		# task='Click "Go cross-site (simple page)" button on https://csreis.github.io/tests/cross-site-iframe.html then tell me the text within',
		# task='Go to https://pirate.github.io/financial-dashboard/iframe.html and click Tools > Edit',
		task="""
			1. Go to https://trailhead.salesforce.com/today (if any login is needed, username: salesforce@sweeting.me password: SfTestPassword992)
			2. Scroll down to Your Personalized Recommendations > Admin Beginner and click the Start button
			3. Click the big blue "Start" button on the left
			4. Scroll all the way to the bottom of the page and under "Choose hands-on org" make sure "My Trailhead Playground 1" is selected, then click "Launch"
			5. Once the org loads in a new tab, click "Dashboards" in the menubar at the top
			6. Click the "teset234234" dashboard and wait till it loads
		""",
		llm=ChatOpenAI(model='gpt-4o', temperature=0.0),
		controller=controller,
		browser=browser,
	)

	await agent.run()
	await browser.close()

	input('Press Enter to close...')


if __name__ == '__main__':
	try:
		asyncio.run(main())
	except Exception as e:
		print(e)
