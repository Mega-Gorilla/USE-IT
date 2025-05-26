import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI

from browser_use import Agent
from browser_use.browser import BrowserProfile, BrowserSession

browser_profile = BrowserProfile(
	# NOTE: you need to close your chrome browser - so that this can open your browser in debug mode
	browser_binary_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
)
browser_session = BrowserSession(browser_profile=browser_profile)


async def main():
	agent = Agent(
		task='In docs.google.com write my Papa a quick letter',
		llm=ChatOpenAI(model='gpt-4o'),
		browser_session=browser_session,
	)

	await agent.run()
	await browser_session.close()

	input('Press Enter to close...')


if __name__ == '__main__':
	asyncio.run(main())
