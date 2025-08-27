from browser_use import Agent, Browser, ChatOpenAI, Tools

browser = Browser()
tools = Tools()
llm = (ChatOpenAI(model='gpt-4.1-mini'),)

agent = Agent(
	task='Ask human for help',
)

agent.run_sync()
