import asyncio

from browser_use import Agent, ChatOpenAI
from examples.integrations.agentmail.email_tools import EmailController

TASK = """
Go to reddit.com, create a new account (use the get_email_address), make up password and all other information, confirm the 2fa, and like latest post on r/elon subreddit.
"""


async def main():
	email_controller = EmailController()
	llm = ChatOpenAI(model='gpt-4.1-mini')
	agent = Agent(task=TASK, controller=email_controller, llm=llm)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
