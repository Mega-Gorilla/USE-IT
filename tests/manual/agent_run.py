"""Manual smoke test for running the Agent end-to-end.

Usage:
    uv run python tests/manual/agent_run.py

The script mirrors the behaviour of the repository root ``test.py`` but stores
the generated history under ``tests/artifacts`` so it does not clutter the
workspace. It is intentionally excluded from automated pytest discovery.
"""

from __future__ import annotations

from pathlib import Path

from browser_use import Agent, ChatGoogle
from browser_use.agent.config import AgentConfig
from browser_use.config import CONFIG


def main() -> None:
	"""Run a manual agent task if API credentials are configured."""
	if not CONFIG.GOOGLE_API_KEY or 'your-google-api-key' in CONFIG.GOOGLE_API_KEY:
		print('Skip manual agent run: GOOGLE_API_KEY is not configured.')
		return

	config = AgentConfig(
		task='browser-use リポジトリのスター数を調査し簡潔に報告してください。',
		llm=ChatGoogle(model='gemini-flash-latest'),
		interactive_mode=False,
		language='jp',
	)

	agent = Agent(config=config)
	result = agent.run_sync(max_steps=10)

	output_dir = Path('tests') / 'artifacts'
	output_dir.mkdir(parents=True, exist_ok=True)
	history_path = output_dir / 'manual_agent_history.json'
	result.save_to_file(history_path)
	print(f'Manual agent history saved to {history_path.resolve()}')


if __name__ == '__main__':
	main()
