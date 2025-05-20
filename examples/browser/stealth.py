import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI

from browser_use import Agent
from browser_use.browser import BrowserProfile, BrowserSession

llm = ChatOpenAI(model='gpt-4o')
browser_session = BrowserSession(
	browser_profile=BrowserProfile(
		headless=False,
		disable_security=False,
		keep_alive=True,
		user_data_dir='~/.config/browseruse/profiles/default',
	)
)


async def main():
	agent = Agent(
		task="""
            Go to https://bot-detector.rebrowser.net/ and verify that all the bot checks are passed.
        """,
		llm=llm,
		browser_session=browser_session,
	)
	await agent.run()
	input('Press Enter to continue to the next test...')

	agent = Agent(
		task="""
            Go to https://www.webflow.com/ and verify that the page is not blocked by a bot check.
        """,
		llm=llm,
		browser_session=browser_session,
	)
	await agent.run()
	input('Press Enter to continue to the next test...')

	agent = Agent(
		task="""
            Go to https://www.okta.com/ and verify that the page is not blocked by a bot check.
        """,
		llm=llm,
		browser_session=browser_session,
	)
	await agent.run()

	agent = Agent(
		task="""
            Go to https://abrahamjuliot.github.io/creepjs/ and verify that the detection score is >50%.
        """,
		llm=llm,
		browser_session=browser_session,
	)
	await agent.run()

	input('Press Enter to close the browser...')

	agent = Agent(
		task="""
            Go to https://nowsecure.nl/ check the "I'm not a robot" checkbox.
        """,
		llm=llm,
		browser_session=browser_session,
	)
	await agent.run()

	input('Press Enter to close the browser...')


if __name__ == '__main__':
	asyncio.run(main())
