from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from browser_use.agent.views import (
	ActionResult,
	AgentHistory,
	AgentHistoryList,
	AgentOutput,
	BrowserStateHistory,
	StepMetadata,
)
from browser_use.browser.views import BrowserStateSummary
from browser_use.dom.views import DOMInteractedElement

if TYPE_CHECKING:
	from browser_use.agent.service import Agent
	from browser_use.tools.registry.views import ActionModel


class HistoryManager:
	"""Manage agent history creation, persistence, and replay."""

	def __init__(self, agent: 'Agent') -> None:
		self.agent = agent

	async def create_history_item(
		self,
		model_output: AgentOutput | None,
		browser_state_summary: BrowserStateSummary,
		result: list[ActionResult],
		metadata: StepMetadata | None = None,
		state_message: str | None = None,
	) -> None:
		agent = self.agent

		if model_output:
			interacted_elements = AgentHistory.get_interacted_element(
				model_output,
				browser_state_summary.dom_state.selector_map,
			)
		else:
			interacted_elements = [None]

		screenshot_path = None
		if browser_state_summary.screenshot:
			agent.logger.debug(
				f'📸 Storing screenshot for step {agent.state.n_steps}, screenshot length: {len(browser_state_summary.screenshot)}'
			)
			screenshot_path = await agent.screenshot_service.store_screenshot(
				browser_state_summary.screenshot,
				agent.state.n_steps,
			)
			agent.logger.debug(f'📸 Screenshot stored at: {screenshot_path}')
		else:
			agent.logger.debug(f'📸 No screenshot in browser_state_summary for step {agent.state.n_steps}')

		state_history = BrowserStateHistory(
			url=browser_state_summary.url,
			title=browser_state_summary.title,
			tabs=browser_state_summary.tabs,
			interacted_element=interacted_elements,
			screenshot_path=screenshot_path,
		)

		history_item = AgentHistory(
			model_output=model_output,
			result=result,
			state=state_history,
			metadata=metadata,
			state_message=state_message,
		)

		agent.history.add_item(history_item)

	async def add_initial_actions_history(self) -> None:
		"""Store the initial actions (step 0) in history if applicable."""
		agent = self.agent

		if not agent.state.last_result or not agent.initial_actions:
			return

		model_output = agent.AgentOutput(
			evaluation_previous_goal='Starting agent with initial actions',
			memory='',
			next_goal='Execute initial navigation or setup actions',
			action=agent.initial_actions,
		)

		metadata = StepMetadata(
			step_number=0,
			step_start_time=time.time(),
			step_end_time=time.time(),
		)

		state_history = BrowserStateHistory(
			url=agent.initial_url or '',
			title='Initial Actions',
			tabs=[],
			interacted_element=[None] * len(agent.initial_actions),
			screenshot_path=None,
		)

		history_item = AgentHistory(
			model_output=model_output,
			result=agent.state.last_result,
			state=state_history,
			metadata=metadata,
		)

		agent.history.add_item(history_item)
		agent.logger.debug('📝 Saved initial actions to history as step 0')

	def save_history(self, file_path: str | Path | None = None) -> None:
		"""Persist history to disk with sensitive data filtering."""
		target = Path(file_path or 'AgentHistory.json')
		self.agent.history.save_to_file(target, sensitive_data=self.agent.sensitive_data)

	async def load_and_rerun(self, history_file: str | Path | None = None, **kwargs) -> list[ActionResult]:
		"""Load a history file and replay it."""
		target = Path(history_file or 'AgentHistory.json')
		history = AgentHistoryList.load_from_file(target, self.agent.AgentOutput)
		return await self.rerun_history(history, **kwargs)

	async def rerun_history(
		self,
		history: AgentHistoryList,
		max_retries: int = 3,
		skip_failures: bool = True,
		delay_between_actions: float = 2.0,
	) -> list[ActionResult]:
		"""Replay saved actions with retry logic."""
		agent = self.agent

		agent.state.session_initialized = True
		await agent.browser_session.start()

		results: list[ActionResult] = []

		for i, history_item in enumerate(history.history):
			goal = history_item.model_output.current_state.next_goal if history_item.model_output else ''
			step_num = history_item.metadata.step_number if history_item.metadata else i
			step_name = 'Initial actions' if step_num == 0 else f'Step {step_num}'
			agent.logger.info(f'Replaying {step_name} ({i + 1}/{len(history.history)}): {goal}')

			if (
				not history_item.model_output
				or not history_item.model_output.action
				or history_item.model_output.action == [None]
			):
				agent.logger.warning(f'{step_name}: No action to replay, skipping')
				results.append(ActionResult(error='No action to replay'))
				continue

			retry_count = 0
			while retry_count < max_retries:
				try:
					step_results = await self._execute_history_step(history_item, delay_between_actions)
					results.extend(step_results)
					break
				except Exception as exc:
					retry_count += 1
					if retry_count == max_retries:
						error_msg = f'{step_name} failed after {max_retries} attempts: {str(exc)}'
						agent.logger.error(error_msg)
						if not skip_failures:
							results.append(ActionResult(error=error_msg))
							raise RuntimeError(error_msg) from exc
					else:
						agent.logger.warning(
							f'{step_name} failed (attempt {retry_count}/{max_retries}), retrying...'
						)
						await asyncio.sleep(delay_between_actions)

		await agent.close()
		return results

	async def _execute_history_step(self, history_item: AgentHistory, delay: float) -> list[ActionResult]:
		"""Execute a replay step with DOM index reconciliation."""
		agent = self.agent
		assert agent.browser_session is not None, 'BrowserSession is not set up'

		state = await agent.browser_session.get_browser_state_summary(include_screenshot=False)
		if not state or not history_item.model_output:
			raise ValueError('Invalid state or model output')

		updated_actions = []
		for i, action in enumerate(history_item.model_output.action):
			updated_action = await self._update_action_indices(
				history_item.state.interacted_element[i],
				action,
				state,
			)
			updated_actions.append(updated_action)

			if updated_action is None:
				raise ValueError(f'Could not find matching element {i} in current page')

		result = await agent.multi_act(updated_actions)

		await asyncio.sleep(delay)
		return result

	async def _update_action_indices(
		self,
		historical_element: DOMInteractedElement | None,
		action: 'ActionModel',
		browser_state_summary: BrowserStateSummary,
	) -> 'ActionModel | None':
		"""Update action indices based on the current DOM."""
		if not historical_element or not browser_state_summary.dom_state.selector_map:
			return action

		match = next(
			(
				(highlight_index, element)
				for highlight_index, element in browser_state_summary.dom_state.selector_map.items()
				if element.element_hash == historical_element.element_hash
			),
			(None, None),
		)
		highlight_index, current_element = match

		if not current_element or highlight_index is None:
			return None

		old_index = action.get_index()
		if old_index != highlight_index:
			self.agent.logger.info(f'Element moved in DOM, updated index from {old_index} to {highlight_index}')
			action.set_index(highlight_index)

		return action
