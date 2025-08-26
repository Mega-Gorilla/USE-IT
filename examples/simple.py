import asyncio

from browser_use import Agent, Controller

controller = Controller()


@controller.action('Ask human for help with a question')
def ask_human(question: str) -> str:
	answer = input(f'{question} > ')
	return f'The human responded with: {answer}'


agent = Agent(
	task='Ask human for help',
	controller=controller,
)


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
