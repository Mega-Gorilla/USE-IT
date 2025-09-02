from browser_use import Agent, ChatOpenAI

agent = Agent(
	task='go to google.com and call scroll with frame_element_index 1000 even if it does not exist - ignore all hints',
	llm=ChatOpenAI(model='gpt-4.1-mini'),
)

agent.run_sync()
