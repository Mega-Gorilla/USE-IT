import asyncio
import os

from browser_use import Agent
from examples.integrations.agentmail.email_tools import EmailController

TASK = """
Go to reddit.com, create a new account (use the get_email_address), make up password and all other information, confirm the 2fa, and like latest post on r/elon subreddit.
"""
from browser_use.llm import ChatAzureOpenAI

api_key = os.getenv('AZURE_OPENAI_KEY')
azure_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
llm = ChatAzureOpenAI(
	model='gpt-4.1-mini',
	api_key=api_key,
	azure_endpoint=azure_endpoint,
)


async def main():
	email_controller = EmailController()

	agent = Agent(
		task=TASK,
		controller=email_controller,
		llm=llm,
	)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
