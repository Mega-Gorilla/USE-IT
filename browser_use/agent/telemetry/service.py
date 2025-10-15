from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Sequence
from urllib.parse import urlparse

from browser_use.telemetry.views import AgentTelemetryEvent
from browser_use.utils import check_latest_browser_use_version

if TYPE_CHECKING:
	from browser_use.agent.service import Agent
	from browser_use.agent.views import ActionResult, AgentOutput
	from browser_use.browser.views import BrowserStateSummary


class TelemetryHandler:
	"""Handle Agent telemetry and related logging."""

	def __init__(self, agent: 'Agent') -> None:
		self.agent = agent

	@property
	def logger(self) -> logging.Logger:
		return self.agent.logger

	async def log_agent_run(self) -> None:
		"""Log task metadata and version information at run start."""
		self.logger.info(f'\033[34m🚀 Task: {self.agent.task}\033[0m')
		self.logger.debug(f'🤖 Browser-Use Library Version {self.agent.version} ({self.agent.source})')

		latest_version = await check_latest_browser_use_version()
		if latest_version and latest_version != self.agent.version:
			self.logger.info(
				f'📦 Newer version available: {latest_version} (current: {self.agent.version}). '
				f'Upgrade with: uv add browser-use@{latest_version}'
			)

	def log_first_step_startup(self) -> None:
		"""Emit a startup log the very first time a step runs."""
		if len(self.agent.history.history) == 0:
			self.logger.info(f'🧠 Starting a browser-use agent with version {self.agent.version} and model={self.agent.llm.model}')

	def log_step_context(self, browser_state_summary: 'BrowserStateSummary') -> None:
		"""Summarise the current browser state for debugging."""
		url = browser_state_summary.url if browser_state_summary else ''
		url_short = url[:50] + '...' if len(url) > 50 else url
		interactive_count = len(browser_state_summary.dom_state.selector_map) if browser_state_summary else 0
		self.logger.info('\n')
		self.logger.info(f'📍 Step {self.agent.state.n_steps}:')
		self.logger.debug(f'Evaluating page with {interactive_count} interactive elements on: {url_short}')

	def log_next_action_summary(self, parsed: 'AgentOutput') -> None:
		"""Log a concise description of the upcoming actions."""
		if not (self.logger.isEnabledFor(logging.DEBUG) and parsed.action):
			return

		action_details: list[str] = []
		for action in parsed.action:
			action_data = action.model_dump(exclude_unset=True)
			action_name = next(iter(action_data.keys())) if action_data else 'unknown'
			action_params = action_data.get(action_name, {}) if action_data else {}

			param_summary = []
			if isinstance(action_params, dict):
				for key, value in action_params.items():
					if key == 'index':
						param_summary.append(f'#{value}')
					elif key == 'text' and isinstance(value, str):
						text_preview = value[:30] + '...' if len(value) > 30 else value
						param_summary.append(f'text="{text_preview}"')
					elif key == 'url':
						param_summary.append(f'url="{value}"')
					elif key == 'success':
						param_summary.append(f'success={value}')
					elif isinstance(value, (str, int, bool)):
						val_str = str(value)[:30] + '...' if len(str(value)) > 30 else str(value)
						param_summary.append(f'{key}={val_str}')

			param_str = f'({", ".join(param_summary)})' if param_summary else ''
			action_details.append(f'{action_name}{param_str}')

		if len(action_details) == 1:
			self.logger.info(f'☝️ Decided next action: {action_details[0]}')
		else:
			summary_lines = [f'✌️ Decided next {len(action_details)} multi-actions:']
			for i, detail in enumerate(action_details):
				summary_lines.append(f'          {i + 1}. {detail}')
			self.logger.info('\n'.join(summary_lines))

	def log_model_response(self, response: 'AgentOutput') -> None:
		"""Log the model's structured response for observability."""
		if response.current_state.thinking:
			self.logger.debug(f'💡 Thinking:\n{response.current_state.thinking}')

		eval_goal = response.current_state.evaluation_previous_goal
		if eval_goal:
			if 'success' in eval_goal.lower():
				emoji = '👍'
				self.logger.info(f'  \033[32m{emoji} Eval: {eval_goal}\033[0m')
			elif 'failure' in eval_goal.lower():
				emoji = '⚠️'
				self.logger.info(f'  \033[31m{emoji} Eval: {eval_goal}\033[0m')
			else:
				emoji = '❔'
				self.logger.info(f'  {emoji} Eval: {eval_goal}')

		if response.current_state.memory:
			self.logger.debug(f'🧠 Memory: {response.current_state.memory}')

		next_goal = response.current_state.next_goal
		if next_goal:
			self.logger.info(f'  \033[34m🎯 Next goal: {next_goal}\033[0m')
		else:
			self.logger.info('')

	def log_step_completion_summary(self, step_start_time: float, result: Sequence['ActionResult']) -> None:
		"""Report per-step duration and success statistics."""
		if not result:
			return

		step_duration = time.time() - step_start_time
		action_count = len(result)
		success_count = sum(1 for r in result if not r.error)
		failure_count = action_count - success_count

		success_indicator = f'✅ {success_count}' if success_count > 0 else ''
		failure_indicator = f'❌ {failure_count}' if failure_count > 0 else ''
		status_parts = [part for part in [success_indicator, failure_indicator] if part]
		status_str = ' | '.join(status_parts) if status_parts else '✅ 0'

		self.logger.debug(
			f'📍 Step {self.agent.state.n_steps}: Ran {action_count} action{"" if action_count == 1 else "s"} '
			f'in {step_duration:.2f}s: {status_str}'
		)

	def log_agent_event(self, max_steps: int, agent_run_error: str | None = None) -> None:
		"""Send aggregated telemetry for the agent run."""
		token_summary = self.agent.token_cost_service.get_usage_tokens_for_model(self.agent.llm.model)

		action_history_data = []
		for item in self.agent.history.history:
			if item.model_output and item.model_output.action:
				step_actions = [
					action.model_dump(exclude_unset=True)
					for action in item.model_output.action
					if action
				]
				action_history_data.append(step_actions)
			else:
				action_history_data.append(None)

		final_res = self.agent.history.final_result()
		final_result_str = json.dumps(final_res) if final_res is not None else None

		self.agent.telemetry.capture(
			AgentTelemetryEvent(
				task=self.agent.task,
				model=self.agent.llm.model,
				model_provider=self.agent.llm.provider,
				max_steps=max_steps,
				max_actions_per_step=self.agent.settings.max_actions_per_step,
				use_vision=self.agent.settings.use_vision,
				version=self.agent.version,
				source=self.agent.source,
				cdp_url=urlparse(self.agent.browser_session.cdp_url).hostname
				if self.agent.browser_session and self.agent.browser_session.cdp_url
				else None,
				action_errors=self.agent.history.errors(),
				action_history=action_history_data,
				urls_visited=self.agent.history.urls(),
				steps=self.agent.state.n_steps,
				total_input_tokens=token_summary.prompt_tokens,
				total_duration_seconds=self.agent.history.total_duration_seconds(),
				success=self.agent.history.is_successful(),
				final_result_response=final_result_str,
				error_message=agent_run_error,
			)
		)

	def log_completion(self) -> None:
		"""Log task completion status."""
		if self.agent.history.is_successful():
			self.logger.info('✅ Task completed successfully')
		else:
			self.logger.info('❌ Task completed without success')
