import asyncio

from browser_use import Agent, ChatGoogle
from browser_use.agent.config import AgentConfig

async def main() -> None:

	config = AgentConfig(
		task='ブラウザ操作で browser-use リポジトリのスター数を調べてください。',
		llm=ChatGoogle(model='gemini-flash-latest'),
		language='jp',
	)

	agent = Agent(config=config)
	await agent.run()

if __name__ == '__main__':
	asyncio.run(main())
