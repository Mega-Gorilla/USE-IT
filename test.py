from browser_use import Agent, ChatGoogle
from browser_use.agent.config import AgentConfig
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    config = AgentConfig(
        task="Find the number of stars of the browser-use repo",
        llm=ChatGoogle(model="gemini-flash-latest"),
    )
    agent = Agent(config=config)
    result = agent.run_sync()
    print(result)


if __name__ == "__main__":
    main()
