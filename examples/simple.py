from browser_use import Agent, ChatOpenAI

agent = Agent(
	task='Give me a csv of 20 collage football games season 2025 ',
	llm=ChatOpenAI(model='gpt-4.1-mini'),
)

agent.run_sync()
