import asyncio

from browser_use import Agent
from examples.integrations.agentmail.controller import EmailController

TASK = """
Go to reddit.com, create a new account (please don't make email, use the get_email_address and use that email address), make up password and all other information, confirm the 2fa, and like latest post on r/elon subreddit.
"""


async def main():
	email_controller = EmailController()

	actions = email_controller.registry.get_prompt_description()

	agent = Agent(
		task=TASK,
		controller=email_controller,
	)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
