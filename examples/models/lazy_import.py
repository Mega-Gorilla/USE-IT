from browser_use import Agent, llm

# available providers for this import style: openai, azure, google
agent = Agent(task='Find founders of browser-use', llm=llm.openai_o3)

agent.run_sync()
