
"""
Simple try of the agent.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import os
import sys

from browser_use.browser import browser
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

from browser_use import Agent, AgentHistoryList, Controller

#llm = ChatOpenAI(model='gpt-4o')
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
)
# browser = Browser(config=BrowserConfig(headless=False))

url = "https://www.autotrader.co.uk/"

task =  """visit {url} on the same tab and look for the following vehicle:
Honda civic, upto 6 years old, manual, petrol, under 15000 pounds, under 60000 miles driven.
It should be within 20 miles of GL51 0BE postal code.

There can be multiple matching cars, extract the list of URLs for each listing. Ensure all the urls are extracted. Some listings have multiple pages, ensure you go through each page and extract the url.

Output format: 
<output>
{{
	"count": [number of cars matched],
	"urls": [
			"[url of match1]",
			"[url of match2]",
			...
		]
}}
</output>
""".format(
	url=url
)

browser_context=BrowserContext(
	browser=Browser(config=BrowserConfig(headless=False, disable_security=True)),
)


agent1 = Agent(
	task=task,
	llm=llm,
	browser_context=browser_context,
	max_actions_per_step=1,
	max_input_tokens=1_000_000
)

async def test_dropdown():
	history: AgentHistoryList = await agent1.run(1000)
	# await controller.browser.close(force=True)

	result = history.final_result()
	assert result is not None
	print("result::", result)
	await browser_context.close()


asyncio.run(test_dropdown())

