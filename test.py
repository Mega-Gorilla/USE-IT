from pathlib import Path

from browser_use import Agent, ChatGoogle
from browser_use.agent.config import AgentConfig


def main() -> None:
    config = AgentConfig(
        task='browser-use リポジトリのスター数を調べてください',
        llm=ChatGoogle(model='gemini-flash-latest'),
        interactive_mode=True,
        language='jp',
    )
    agent = Agent(config=config)
    result = agent.run_sync()

    history_path = Path('temp') / 'agent_history.json'
    result.save_to_file(history_path)
    print(f'History saved to {history_path.resolve()}')


if __name__ == "__main__":
    main()
