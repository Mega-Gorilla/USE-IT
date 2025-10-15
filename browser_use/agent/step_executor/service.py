from __future__ import annotations

import asyncio
import json
import inspect
import logging
import textwrap
import time
from typing import TYPE_CHECKING

from browser_use.agent.cloud_events import CreateAgentStepEvent
from browser_use.agent.views import ActionResult, AgentError, AgentStepInfo, ApprovalResult, StepMetadata
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.messages import UserMessage
from browser_use.observability import observe, observe_debug
from browser_use.utils import time_execution_async

# Rich library for beautiful console UI (optional dependency from cli extras)
try:
	from rich.console import Console
	from rich.panel import Panel
	from rich.table import Table
	from rich.prompt import Prompt
	from rich import box

	RICH_AVAILABLE = True
except ImportError:
	RICH_AVAILABLE = False

if TYPE_CHECKING:
	from browser_use.agent.service import Agent
	from browser_use.tools.registry.views import ActionModel


class StepExecutor:
	"""Coordinate the main control flow of Agent steps."""

	def __init__(self, agent: 'Agent') -> None:
		self.agent = agent

	@observe(name='agent.step', ignore_output=True, ignore_input=True)
	@time_execution_async('--step')
	async def execute_step(self, step_info: AgentStepInfo | None = None) -> None:
		"""Execute a full step lifecycle."""
		agent = self.agent
		agent.step_start_time = time.time()
		browser_state_summary: BrowserStateSummary | None = None
		try:
			browser_state_summary = await self.prepare_context(step_info)

			while True:
				await self.get_next_action(browser_state_summary)

				if not agent.settings.interactive_mode:
					break

				approval = await self.request_human_approval(step_info, browser_state_summary)

				if approval.decision == 'approve':
					agent.logger.debug('✅ Interactive approval granted - executing actions')
					break

				if approval.decision == 'retry':
					if approval.feedback:
						agent.logger.info('🔁 人間からのフィードバックを受け取り、再度アクション候補を生成します')
						agent._message_manager._add_context_message(
							UserMessage(content=f'<human_feedback>{approval.feedback}</human_feedback>')
						)
					else:
						agent.logger.info('🔁 承認されなかったため、フィードバックなしでアクションを再生成します')

					await agent._check_stop_or_pause()
					continue

				if approval.decision == 'skip':
					agent.logger.info('⏭️ ユーザーがこのステップのアクション実行をスキップしました（インタラクティブモード）')
					agent.state.last_result = [
						ActionResult(
							extracted_content='User skipped execution during interactive approval.',
							include_in_memory=True,
							long_term_memory='User skipped execution during interactive approval.',
						)
					]
					return

				if approval.decision == 'cancel':
					agent.logger.info('🛑 ユーザーがインタラクティブモードでエージェント実行をキャンセルしました')
					agent.stop()
					agent.state.last_result = [
						ActionResult(
							error='User cancelled execution during interactive approval.',
						)
					]
					return

			await self.execute_actions()
			await self.post_process()
		except Exception as exc:
			await self.handle_step_error(exc)
		finally:
			await self.finalize(browser_state_summary)

	async def request_human_approval(
		self,
		step_info: AgentStepInfo | None,
		browser_state_summary: BrowserStateSummary,
	) -> ApprovalResult:
		"""Request human approval before executing actions (アクション実行前に承認を取得)."""
		agent = self.agent
		model_output = agent.state.last_model_output

		if model_output is None or not model_output.action:
			agent.logger.debug('🛈 No actions proposed; skipping approval check')
			return ApprovalResult(decision='approve')

		approval_callback = getattr(agent, 'approval_callback', None)
		if approval_callback:
			raw_result = approval_callback(step_info, model_output, browser_state_summary)
			if inspect.isawaitable(raw_result):
				raw_result = await raw_result  # type: ignore[assignment]
			return self._normalize_approval_result(raw_result)

		return await self._console_approval_interface(step_info, model_output, browser_state_summary)

	def _normalize_approval_result(self, value: ApprovalResult | tuple[bool, str | None]) -> ApprovalResult:
		if isinstance(value, ApprovalResult):
			return value
		if isinstance(value, tuple) and len(value) == 2:
			return ApprovalResult.from_tuple(value)
		raise TypeError(
			'approval_callback must return ApprovalResult or tuple[bool, str | None]'
		)

	async def _console_approval_interface(
		self,
		step_info: AgentStepInfo | None,
		model_output,
		browser_state_summary: BrowserStateSummary,
	) -> ApprovalResult:
		"""Default console-based approval flow (デフォルト対話UI)."""
		if RICH_AVAILABLE:
			return await self._console_approval_interface_rich(step_info, model_output, browser_state_summary)
		else:
			return await self._console_approval_interface_fallback(step_info, model_output, browser_state_summary)

	async def _console_approval_interface_rich(
		self,
		step_info: AgentStepInfo | None,
		model_output,
		browser_state_summary: BrowserStateSummary,
	) -> ApprovalResult:
		"""Rich-based beautiful console UI for approval flow."""
		agent = self.agent
		actions = model_output.action
		console = Console()

		if not actions:
			return ApprovalResult(decision='approve')

		# ステップ情報の準備
		if step_info:
			current_step = step_info.step_number + 1
			max_steps = step_info.max_steps
			step_label = f'{current_step}/{max_steps}'
		else:
			step_label = str(agent.state.n_steps)

		# URL短縮
		url_display = browser_state_summary.url
		if len(url_display) > 80:
			url_display = url_display[:77] + '...'

		# ヘッダーパネル
		console.print()
		console.print(
			Panel(
				f'[bold cyan]🤖 Interactive Mode - Action Approval[/bold cyan]\n\n'
				f'📍 Step: [yellow]{step_label}[/yellow]\n'
				f'🌐 URL: [blue]{url_display}[/blue]',
				border_style='cyan',
				box=box.DOUBLE,
			)
		)

		# アクションテーブル
		action_count = len(actions)
		table = Table(
			title=f'📋 Proposed {action_count} action{"s" if action_count > 1 else ""}',
			box=box.ROUNDED,
			show_header=True,
			header_style='bold magenta',
			title_style='bold',
		)
		table.add_column('No.', style='cyan', width=4, justify='center')
		table.add_column('Action', style='green', width=20)
		table.add_column('Parameters', style='yellow')

		for idx, action in enumerate(actions, 1):
			dumped = action.model_dump(exclude_unset=True)
			action_name, action_body = next(iter(dumped.items()), ('unknown', dumped))

			# パラメータを整形
			if isinstance(action_body, dict):
				params_lines = []
				for key, value in action_body.items():
					value_str = str(value)
					if len(value_str) > 60:
						value_str = value_str[:57] + '...'
					params_lines.append(f'• {key}: {value_str}')
				params = '\n'.join(params_lines)
			else:
				params = str(action_body)
				if len(params) > 60:
					params = params[:57] + '...'

			table.add_row(str(idx), action_name, params)

		console.print(table)

		# 選択肢の表示
		console.print('\n[bold]Choose an option:[/bold]')
		console.print('  [green][a][/green] Approve - Execute this action')
		console.print('  [yellow][r][/yellow] Retry - Provide feedback to regenerate')
		console.print('  [blue][s][/blue] Skip - Skip this step')
		console.print('  [red][c][/red] Cancel - Stop the agent\n')

		# 入力ループ
		while True:
			try:
				choice = await asyncio.to_thread(
					Prompt.ask, '👉 Your choice', choices=['a', 'r', 's', 'c'], default='a'
				)
			except (EOFError, KeyboardInterrupt):
				console.print('\n🛑 [red]Input interrupted. Cancelling agent.[/red]\n')
				return ApprovalResult(decision='cancel')

			if choice in {'a', 'approve', 'y', 'yes'}:
				console.print('✅ [green]Approved. Executing action...[/green]\n')
				return ApprovalResult(decision='approve')

			if choice in {'s', 'skip'}:
				console.print('⏭️  [blue]Skipping this step.[/blue]\n')
				return ApprovalResult(decision='skip')

			if choice in {'c', 'cancel', 'q', 'quit'}:
				console.print('🛑 [red]Stopping agent.[/red]\n')
				return ApprovalResult(decision='cancel')

			if choice in {'r', 'reject', 'retry'}:
				console.print(
					Panel(
						'Describe what should be changed (leave empty to cancel)',
						title='💬 Feedback to LLM',
						border_style='yellow',
					)
				)
				try:
					feedback = await asyncio.to_thread(Prompt.ask, 'Feedback')
				except (EOFError, KeyboardInterrupt):
					console.print('\n🛑 [red]Feedback input interrupted. Cancelling agent.[/red]\n')
					return ApprovalResult(decision='cancel')

				feedback = feedback.strip()
				if not feedback:
					console.print('⚠️  [yellow]Feedback is empty. Please try again.[/yellow]\n')
					continue

				console.print('🔁 [yellow]Feedback received. Asking LLM to regenerate action...[/yellow]\n')
				return ApprovalResult(decision='retry', feedback=feedback)

			console.print('❌ [red]Invalid choice. Please enter [a/r/s/c].[/red]\n')

	async def _console_approval_interface_fallback(
		self,
		step_info: AgentStepInfo | None,
		model_output,
		browser_state_summary: BrowserStateSummary,
	) -> ApprovalResult:
		"""Fallback console UI using plain print() when Rich is not available."""
		agent = self.agent
		actions = model_output.action
		if not actions:
			return ApprovalResult(decision='approve')

		# ステップ番号の決定
		if step_info:
			current_step = step_info.step_number + 1
			max_steps = step_info.max_steps
			step_label = f'{current_step}/{max_steps}'
		else:
			step_label = str(agent.state.n_steps)

		# URL短縮
		url_display = browser_state_summary.url
		if len(url_display) > 60:
			url_display = url_display[:57] + '...'

		# ボックスUIの表示
		print()
		print('╔══════════════════════════════════════════════════════════════╗')
		print('║         🤖 Interactive Mode - Action Approval                ║')
		print('╠══════════════════════════════════════════════════════════════╣')
		print(f'║  📍 Step: {step_label:<51} ║')
		print(f'║  🌐 URL: {url_display:<52} ║')
		print('╠══════════════════════════════════════════════════════════════╣')
		action_count = len(actions)
		action_label = 'action' if action_count == 1 else 'actions'
		print(f'║  📋 Proposed {action_count} {action_label}{" " * (43 - len(str(action_count)) - len(action_label))}║')
		print('║                                                              ║')

		# 各アクションを表示
		for idx, action in enumerate(actions, 1):
			dumped = action.model_dump(exclude_unset=True)
			action_name, action_body = next(iter(dumped.items()), ('unknown', dumped))
			print(f'║  ▶ Action {idx}: {action_name:<47} ║')

			if isinstance(action_body, dict):
				for key, value in action_body.items():
					value_str = str(value)
					max_value_len = 48
					if len(value_str) > max_value_len:
						value_str = value_str[:max_value_len - 3] + '...'
					param_line = f'{key}: {value_str}'
					display_width = len(param_line.encode('utf-8')) - len(param_line)
					padding = max(0, 54 - len(param_line) - display_width // 2)
					print(f'║    • {param_line}{" " * padding}║')
			else:
				value_str = str(action_body)
				if len(value_str) > 54:
					value_str = value_str[:51] + '...'
				display_width = len(value_str.encode('utf-8')) - len(value_str)
				padding = max(0, 58 - len(value_str) - display_width // 2)
				print(f'║    {value_str}{" " * padding}║')

			if idx < len(actions):
				print('║                                                              ║')

		print('╠══════════════════════════════════════════════════════════════╣')
		print('║  Choose an option:                                           ║')
		print('║    [a] Approve - Execute this action                         ║')
		print('║    [r] Retry - Provide feedback to regenerate action         ║')
		print('║    [s] Skip - Skip this step entirely                        ║')
		print('║    [c] Cancel - Stop the agent                               ║')
		print('╚══════════════════════════════════════════════════════════════╝')
		print()

		while True:
			try:
				choice = (await asyncio.to_thread(input, '\n👉 Your choice [a/r/s/c]: ')).strip().lower()
			except (EOFError, KeyboardInterrupt):
				print('\n🛑 Input interrupted. Cancelling agent.\n')
				return ApprovalResult(decision='cancel')

			if choice in {'a', 'approve', 'y', 'yes'}:
				print('✅ Approved. Executing action...\n')
				return ApprovalResult(decision='approve')

			if choice in {'s', 'skip'}:
				print('⏭️  Skipping this step.\n')
				return ApprovalResult(decision='skip')

			if choice in {'c', 'cancel', 'q', 'quit'}:
				print('🛑 Stopping agent.\n')
				return ApprovalResult(decision='cancel')

			if choice in {'r', 'reject', 'retry'}:
				print('\n┌──────────────────────────────────────────────────────────┐')
				print('│ Feedback to LLM (describe what should be changed)       │')
				print('└──────────────────────────────────────────────────────────┘')
				try:
					feedback = (await asyncio.to_thread(input, '💬 Feedback: ')).strip()
				except (EOFError, KeyboardInterrupt):
					print('\n🛑 Feedback input interrupted. Cancelling agent.\n')
					return ApprovalResult(decision='cancel')

				if not feedback:
					print('⚠️  Feedback is empty. Please try again.\n')
					continue
				print('🔁 Feedback received. Asking LLM to regenerate action...\n')
				return ApprovalResult(decision='retry', feedback=feedback)

			print('❌ Invalid choice. Please enter [a/r/s/c].\n')

	async def prepare_context(self, step_info: AgentStepInfo | None = None) -> BrowserStateSummary:
		agent = self.agent
		assert agent.browser_session is not None, 'BrowserSession is not set up'

		agent.logger.debug(f'🌐 Step {agent.state.n_steps}: Getting browser state...')
		agent.logger.debug('📸 Requesting browser state with include_screenshot=True')
		browser_state_summary = await agent.browser_session.get_browser_state_summary(
			include_screenshot=True,
			include_recent_events=agent.include_recent_events,
		)

		if browser_state_summary.screenshot:
			agent.logger.debug(f'📸 Got browser state WITH screenshot, length: {len(browser_state_summary.screenshot)}')
		else:
			agent.logger.debug('📸 Got browser state WITHOUT screenshot')

		await agent._check_and_update_downloads(f'Step {agent.state.n_steps}: after getting browser state')

		agent.telemetry_handler.log_step_context(browser_state_summary)
		await agent._check_stop_or_pause()

		agent.logger.debug(f'📝 Step {agent.state.n_steps}: Updating action models...')
		await agent._update_action_models_for_page(browser_state_summary.url)

		page_filtered_actions = agent.tools.registry.get_prompt_description(browser_state_summary.url)

		agent.logger.debug(f'💬 Step {agent.state.n_steps}: Creating state messages for context...')
		agent._message_manager.create_state_messages(
			browser_state_summary=browser_state_summary,
			model_output=agent.state.last_model_output,
			result=agent.state.last_result,
			step_info=step_info,
			use_vision=agent.settings.use_vision,
			page_filtered_actions=page_filtered_actions if page_filtered_actions else None,
			sensitive_data=agent.sensitive_data,
			available_file_paths=agent.available_file_paths,
		)

		await self.force_done_after_last_step(step_info)
		await self.force_done_after_failure()
		return browser_state_summary

	@observe_debug(ignore_input=True, name='get_next_action')
	async def get_next_action(self, browser_state_summary: BrowserStateSummary) -> None:
		agent = self.agent
		input_messages = agent._message_manager.get_messages()
		agent.logger.debug(
			f'🤖 Step {agent.state.n_steps}: Calling LLM with {len(input_messages)} messages (model: {agent.llm.model})...'
		)

		try:
			model_output = await asyncio.wait_for(
				agent.llm_handler.get_model_output_with_retry(input_messages), timeout=agent.settings.llm_timeout
			)
		except TimeoutError:
			raise TimeoutError(
				f'LLM call timed out after {agent.settings.llm_timeout} seconds. Keep your thinking and output short.'
			)

		agent.state.last_model_output = model_output
		await agent._check_stop_or_pause()
		await agent.llm_handler.handle_post_llm_processing(browser_state_summary, input_messages)
		await agent._check_stop_or_pause()

	async def execute_actions(self) -> None:
		agent = self.agent
		if agent.state.last_model_output is None:
			raise ValueError('No model output to execute actions from')

		agent.logger.debug(
			f'⚡ Step {agent.state.n_steps}: Executing {len(agent.state.last_model_output.action)} actions...'
		)
		result = await self.multi_act(agent.state.last_model_output.action)
		agent.logger.debug(f'✅ Step {agent.state.n_steps}: Actions completed')
		agent.state.last_result = result

	async def post_process(self) -> None:
		agent = self.agent
		assert agent.browser_session is not None, 'BrowserSession is not set up'

		await agent._check_and_update_downloads('after executing actions')

		if agent.state.last_result and len(agent.state.last_result) == 1 and agent.state.last_result[-1].error:
			agent.state.consecutive_failures += 1
			agent.logger.debug(f'🔄 Step {agent.state.n_steps}: Consecutive failures: {agent.state.consecutive_failures}')
			return

		agent.state.consecutive_failures = 0
		agent.logger.debug(f'🔄 Step {agent.state.n_steps}: Consecutive failures reset to: {agent.state.consecutive_failures}')

		if agent.state.last_result and len(agent.state.last_result) > 0 and agent.state.last_result[-1].is_done:
			success = agent.state.last_result[-1].success
			if success:
				agent.logger.info(f'\n📄 \033[32m Final Result:\033[0m \n{agent.state.last_result[-1].extracted_content}\n\n')
			else:
				agent.logger.info(f'\n📄 \033[31m Final Result:\033[0m \n{agent.state.last_result[-1].extracted_content}\n\n')
			if agent.state.last_result[-1].attachments:
				total_attachments = len(agent.state.last_result[-1].attachments)
				for i, file_path in enumerate(agent.state.last_result[-1].attachments):
					agent.logger.info(f'👉 Attachment {i + 1 if total_attachments > 1 else ""}: {file_path}')

	async def handle_step_error(self, error: Exception) -> None:
		agent = self.agent

		if isinstance(error, InterruptedError):
			error_msg = 'The agent was interrupted mid-step' + (f' - {str(error)}' if str(error) else '')
			agent.logger.error(f'{error_msg}')
			return

		include_trace = agent.logger.isEnabledFor(logging.DEBUG)
		error_msg = AgentError.format_error(error, include_trace=include_trace)
		prefix = (
			f'❌ Result failed {agent.state.consecutive_failures + 1}/'
			f'{agent.settings.max_failures + int(agent.settings.final_response_after_failure)} times:\n '
		)
		agent.state.consecutive_failures += 1

		if 'Could not parse response' in error_msg or 'tool_use_failed' in error_msg:
			agent.logger.error(f'Model: {agent.llm.model} failed')
			agent.logger.error(f'{prefix}{error_msg}')
		else:
			agent.logger.error(f'{prefix}{error_msg}')

		agent.state.last_result = [ActionResult(error=error_msg)]

	async def finalize(self, browser_state_summary: BrowserStateSummary | None) -> None:
		agent = self.agent
		step_end_time = time.time()
		if not agent.state.last_result:
			return

		if browser_state_summary:
			metadata = StepMetadata(
				step_number=agent.state.n_steps,
				step_start_time=agent.step_start_time,
				step_end_time=step_end_time,
			)

			await agent.history_manager.create_history_item(
				agent.state.last_model_output,
				browser_state_summary,
				agent.state.last_result,
				metadata,
				state_message=agent._message_manager.last_state_message_text,
			)

		agent.telemetry_handler.log_step_completion_summary(agent.step_start_time, agent.state.last_result)
		agent.save_file_system_state()

		if browser_state_summary and agent.state.last_model_output:
			actions_data = []
			if agent.state.last_model_output.action:
				for action in agent.state.last_model_output.action:
					action_dict = action.model_dump() if hasattr(action, 'model_dump') else {}
					actions_data.append(action_dict)

			if agent.enable_cloud_sync:
				step_event = CreateAgentStepEvent.from_agent_step(
					agent,
					agent.state.last_model_output,
					agent.state.last_result,
					actions_data,
					browser_state_summary,
				)
				agent.eventbus.dispatch(step_event)

		agent.state.n_steps += 1

	async def force_done_after_last_step(self, step_info: AgentStepInfo | None = None) -> None:
		agent = self.agent
		if step_info and step_info.is_last_step():
			msg = 'Now comes your last step. Use only the "done" action now. No other actions - so here your action sequence must have length 1.'
			msg += '\nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed.'
			msg += '\nIf the task is fully finished, set success in "done" to true.'
			msg += '\nInclude everything you found out for the ultimate task in the done text.'
			agent.logger.debug('Last step finishing up')
			agent._message_manager._add_context_message(UserMessage(content=msg))
			agent.AgentOutput = agent.DoneAgentOutput

	async def force_done_after_failure(self) -> None:
		agent = self.agent
		if agent.state.consecutive_failures >= agent.settings.max_failures and agent.settings.final_response_after_failure:
			msg = f'You have failed {agent.settings.max_failures} consecutive times. This is your final step to complete the task or provide what you found. '
			msg += 'Use only the "done" action now. No other actions - so here your action sequence must have length 1.'
			msg += '\nIf the task could not be completed due to the failures, set success in "done" to false!'
			msg += '\nInclude everything you found out for the task in the done text.'

			agent.logger.debug('Force done action, because we reached max_failures.')
			agent._message_manager._add_context_message(UserMessage(content=msg))
			agent.AgentOutput = agent.DoneAgentOutput

	async def execute_initial_actions(self) -> None:
		agent = self.agent
		if agent.initial_actions and not agent.state.follow_up_task:
			agent.logger.debug(f'⚡ Executing {len(agent.initial_actions)} initial actions...')
			result = await self.multi_act(agent.initial_actions, check_for_new_elements=False)
			if result and agent.initial_url and result[0].long_term_memory:
				result[0].long_term_memory = f'Found initial url and automatically loaded it. {result[0].long_term_memory}'
			agent.state.last_result = result

			await agent.history_manager.add_initial_actions_history()
			agent.logger.debug('Initial actions completed')

	@observe_debug(ignore_input=True, ignore_output=True)
	@time_execution_async('--multi_act')
	async def multi_act(
		self,
		actions: list['ActionModel'],
		check_for_new_elements: bool = True,
	) -> list[ActionResult]:
		agent = self.agent
		results: list[ActionResult] = []

		assert agent.browser_session is not None, 'BrowserSession is not set up'
		try:
			if (
				agent.browser_session._cached_browser_state_summary is not None
				and agent.browser_session._cached_browser_state_summary.dom_state is not None
			):
				cached_selector_map = dict(agent.browser_session._cached_browser_state_summary.dom_state.selector_map)
				cached_element_hashes = {e.parent_branch_hash() for e in cached_selector_map.values()}
			else:
				cached_selector_map = {}
				cached_element_hashes = set()
		except Exception as exc:
			agent.logger.error(f'Error getting cached selector map: {exc}')
			cached_selector_map = {}
			cached_element_hashes = set()

		total_actions = len(actions)

		for i, action in enumerate(actions):
			if i > 0 and action.model_dump(exclude_unset=True).get('done') is not None:
				msg = f'Done action is allowed only as a single action - stopped after action {i} / {total_actions}.'
				agent.logger.debug(msg)
				break

			if action.get_index() is not None and i != 0:
				new_browser_state_summary = await agent.browser_session.get_browser_state_summary(
					include_screenshot=False,
				)
				new_selector_map = new_browser_state_summary.dom_state.selector_map

				def get_remaining_actions_str(actions: list['ActionModel'], index: int) -> str:
					remaining_actions = []
					for remaining_action in actions[index:]:
						action_data = remaining_action.model_dump(exclude_unset=True)
						action_name = next(iter(action_data.keys())) if action_data else 'unknown'
						remaining_actions.append(action_name)
					return ', '.join(remaining_actions)

				orig_target = cached_selector_map.get(action.get_index())
				orig_target_hash = orig_target.parent_branch_hash() if orig_target else None

				new_target = new_selector_map.get(action.get_index())  # type: ignore
				new_target_hash = new_target.parent_branch_hash() if new_target else None

				if orig_target_hash != new_target_hash:
					remaining_actions_str = get_remaining_actions_str(actions, i)
					msg = f'Page changed after action: actions {remaining_actions_str} are not yet executed'
					agent.logger.info(msg)
					results.append(
						ActionResult(
							extracted_content=msg,
							include_in_memory=True,
							long_term_memory=msg,
						)
					)
					break

				new_element_hashes = {e.parent_branch_hash() for e in new_selector_map.values()}
				if check_for_new_elements and not new_element_hashes.issubset(cached_element_hashes):
					agent.logger.debug(f'New elements: {abs(len(new_element_hashes) - len(cached_element_hashes))}')
					remaining_actions_str = get_remaining_actions_str(actions, i)
					msg = f'Something new appeared after action {i} / {total_actions}: actions {remaining_actions_str} were not executed'
					agent.logger.info(msg)
					results.append(
						ActionResult(
							extracted_content=msg,
							include_in_memory=True,
							long_term_memory=msg,
						)
					)
					break

			if i > 0:
				await asyncio.sleep(agent.browser_profile.wait_between_actions)

			red = '\033[91m'
			green = '\033[92m'
			cyan = '\033[96m'
			blue = '\033[34m'
			reset = '\033[0m'

			try:
				await agent._check_stop_or_pause()
				action_data = action.model_dump(exclude_unset=True)
				action_name = next(iter(action_data.keys())) if action_data else 'unknown'
				action_params = getattr(action, action_name, '') or str(action.model_dump(mode='json'))[:140].replace(
					'"', ''
				).replace('{', '').replace('}', '').replace("'", '').strip().strip(',')
				action_params = str(action_params)
				action_params = f'{action_params[:522]}...' if len(action_params) > 528 else action_params
				time_start = time.time()
				agent.logger.info(f'  🦾 {blue}[ACTION {i + 1}/{total_actions}]{reset} {action_params}')

				result = await agent.tools.act(
					action=action,
					browser_session=agent.browser_session,
					file_system=agent.file_system,
					page_extraction_llm=agent.settings.page_extraction_llm,
					sensitive_data=agent.sensitive_data,
					available_file_paths=agent.available_file_paths,
				)

				time_end = time.time()
				time_elapsed = time_end - time_start
				results.append(result)

				agent.logger.debug(
					f'☑️ Executed action {i + 1}/{total_actions}: {green}{action_params}{reset} in {time_elapsed:.2f}s'
				)

				if results[-1].is_done or results[-1].error or i == total_actions - 1:
					break

			except Exception as exc:
				agent.logger.error(f'❌ Executing action {i + 1} failed -> {type(exc).__name__}: {exc}')
				raise

		return results

	async def take_step(self, step_info: AgentStepInfo | None = None) -> tuple[bool, bool]:
		agent = self.agent
		if step_info is not None and step_info.step_number == 0:
			agent.telemetry_handler.log_first_step_startup()
			try:
				await self.execute_initial_actions()
			except InterruptedError:
				pass

		await self.execute_step(step_info)

		if agent.history.is_done():
			agent.telemetry_handler.log_completion()
			if agent.register_done_callback:
				if inspect.iscoroutinefunction(agent.register_done_callback):
					await agent.register_done_callback(agent.history)
				else:
					agent.register_done_callback(agent.history)
			return True, True

		return False, False
