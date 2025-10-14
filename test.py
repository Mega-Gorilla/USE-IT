from browser_use import Agent, ChatGoogle
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    agent = Agent(
        task="Find the number of stars of the browser-use repo",
        llm=ChatGoogle(model="gemini-flash-latest"),
    )
    result = agent.run_sync()
    print(result)


if __name__ == "__main__":
    main()
