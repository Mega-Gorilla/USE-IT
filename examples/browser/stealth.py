import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from imgcat import imgcat
from langchain_openai import ChatOpenAI
from patchright.async_api import async_playwright as async_patchright
from playwright.async_api import async_playwright

from browser_use.browser import BrowserSession

llm = ChatOpenAI(model='gpt-4o')

terminal_width, terminal_height = shutil.get_terminal_size((80, 20))


async def main():
	browser_session = BrowserSession(
		playwright=await async_playwright().start(),
		headless=False,
		disable_security=True,
		user_data_dir=None,
		deterministic_rendering=True,
	)

	stealth_browser_session = BrowserSession(
		# cdp_url='wss://browser.zenrows.com?apikey=your-api-key-here&proxy_region=na',
		#                or try anchor browser, browserless, steel.dev, browserbase, oxylabs, brightdata, etc.
		playwright=await async_patchright().start(),
		headless=False,
		disable_security=False,
		user_data_dir='~/.config/browseruse/profiles/stealth',
		deterministic_rendering=False,
	)
	await browser_session.start()
	await stealth_browser_session.start()
	await browser_session.create_new_tab('https://abrahamjuliot.github.io/creepjs/')
	await stealth_browser_session.create_new_tab('https://abrahamjuliot.github.io/creepjs/')
	await asyncio.sleep(5)
	await (await browser_session.get_current_page()).screenshot(path='normal_browser.png')
	print('NORMAL BROWSER:')
	imgcat(Path('normal_browser.png').read_bytes(), height=max(terminal_height - 15, 40))
	print()
	print()

	print('STEALTH BROWSER:')
	await (await stealth_browser_session.get_current_page()).screenshot(path='stealth_browser.png')
	imgcat(Path('stealth_browser.png').read_bytes(), height=max(terminal_height - 15, 40))
	# agent = Agent(
	# 	task="""
	#         Go to https://abrahamjuliot.github.io/creepjs/ and verify that the detection score is >50%.
	#     """,
	# 	llm=llm,
	# 	browser_session=browser_session,
	# )
	# await agent.run()

	# input('Press Enter to close the browser...')

	# agent = Agent(
	# 	task="""
	#         Go to https://bot-detector.rebrowser.net/ and verify that all the bot checks are passed.
	#     """,
	# 	llm=llm,
	# 	browser_session=browser_session,
	# )
	# await agent.run()
	# input('Press Enter to continue to the next test...')

	# agent = Agent(
	# 	task="""
	#         Go to https://www.webflow.com/ and verify that the page is not blocked by a bot check.
	#     """,
	# 	llm=llm,
	# 	browser_session=browser_session,
	# )
	# await agent.run()
	# input('Press Enter to continue to the next test...')

	# agent = Agent(
	# 	task="""
	#         Go to https://www.okta.com/ and verify that the page is not blocked by a bot check.
	#     """,
	# 	llm=llm,
	# 	browser_session=browser_session,
	# )
	# await agent.run()

	# agent = Agent(
	# 	task="""
	#         Go to https://nowsecure.nl/ check the "I'm not a robot" checkbox.
	#     """,
	# 	llm=llm,
	# 	browser_session=browser_session,
	# )
	# await agent.run()

	input('Press Enter to close the browser...')


if __name__ == '__main__':
	asyncio.run(main())
