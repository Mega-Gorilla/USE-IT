import asyncio
import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr

from browser_use import Agent
from browser_use.browser.browser import Browser, BrowserConfig

load_dotenv()
api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
	raise ValueError('GEMINI_API_KEY is not set')

llm = ChatGoogleGenerativeAI(model='gemini-2.0-flash-exp', api_key=SecretStr(api_key))

browser = Browser(
	config=BrowserConfig(
		# NOTE: you need to close your chrome browser - so that this can open your browser in debug mode
		chrome_instance_path='C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
	)
)

async def run_search():
	agent = Agent(
		task=(
			'search for "lorem ipsum pdf file" on google and stream the first pdf file and return its first paragraph'
		),
		llm=llm,
		max_actions_per_step=4,
		browser=browser,
	)

	await agent.run(max_steps=25)


if __name__ == '__main__':
	asyncio.run(run_search())
