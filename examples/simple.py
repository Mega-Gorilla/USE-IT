from dotenv import load_dotenv

from browser_use import Agent, ChatGoogle
from browser_use.agent.config import AgentConfig

load_dotenv()

config = AgentConfig(
	task='Find the number of stars of the browser-use repo',
	llm=ChatGoogle(model='gemini-flash-latest'),
	# browser=Browser(use_cloud=True),  # Uses Browser-Use cloud for the browser
)
agent = Agent(config=config)
agent.run_sync()
