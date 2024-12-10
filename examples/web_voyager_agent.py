import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import os

from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from browser_use.agent.service import Agent
from browser_use.browser.browser import Browser, BrowserConfig, BrowserContextConfig

browser = Browser(
	config=BrowserConfig(
		headless=False,  # This is True in production
		disable_security=True,
		new_context_config=BrowserContextConfig(
			disable_security=True,
			minimum_wait_page_load_time=4,
			maximum_wait_page_load_time=20,
			# no_viewport=True,
			browser_window_size={
				'width': 1280,
				'height': 1100,
			},
			# trace_path='./tmp/web_voyager_agent',
		),
	)
)
llm = AzureChatOpenAI(
	model='gpt-4o',
	api_version='2024-10-21',
	azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT', ''),
	api_key=SecretStr(os.getenv('AZURE_OPENAI_KEY', '')),
)

# TASK = """
# Find the lowest-priced one-way flight from Cairo to Montreal on February 21, 2025, including the total travel time and number of stops. on https://www.google.com/travel/flights/
# """
# TASK = """
# Browse Coursera, which universities offer Master of Advanced Study in Engineering degrees? Tell me what is the latest application deadline for this degree? on https://www.coursera.org/"""
TASK = """
Find the names of people in ArXiv's Leadership Team. on https://arxiv.org/
"""


async def main():
	agent = Agent(
		task=TASK,
		llm=llm,
		browser=browser,
		validate_output=True,
	)
	history = await agent.run(max_steps=50)
	history.save_to_file('./tmp/history.json')


if __name__ == '__main__':
	asyncio.run(main())
