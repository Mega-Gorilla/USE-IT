from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from browser_use.agent.cloud_events import (
	CreateAgentOutputFileEvent,
	CreateAgentSessionEvent,
	CreateAgentTaskEvent,
	UpdateAgentTaskEvent,
)
from browser_use.agent.views import (
	ActionResult,
	AgentHistory,
	AgentHistoryList,
	AgentStepInfo,
	BrowserStateHistory,
)
from browser_use.observability import observe
from browser_use.utils import SignalHandler, time_execution_async

AgentHookFunc = Callable[['Agent'], Awaitable[None]]


class AgentRunner:
	"""Coordinate the main Agent execution loop."""

	def __init__(self, agent: 'Agent') -> None:
		self.agent = agent
		self._force_exit_telemetry_logged = False

	@observe(name='agent.run', metadata={'task': '{{task}}', 'debug': '{{debug}}'})
	@time_execution_async('--run')
	async def run(
		self,
		max_steps: int = 100,
		on_step_start: AgentHookFunc | None = None,
		on_step_end: AgentHookFunc | None = None,
	) -> AgentHistoryList:
		agent_run_error: str | None = None
		loop = asyncio.get_event_loop()

		def on_force_exit_log_telemetry():
			self.agent.telemetry_handler.log_agent_event(max_steps=max_steps, agent_run_error='SIGINT: Cancelled by user')
			if hasattr(self.agent, 'telemetry') and self.agent.telemetry:
				self.agent.telemetry.flush()
			self._force_exit_telemetry_logged = True

		signal_handler = SignalHandler(
			loop=loop,
			pause_callback=self.agent.pause,
			resume_callback=self.agent.resume,
			custom_exit_callback=on_force_exit_log_telemetry,
			exit_on_second_int=True,
		)
		signal_handler.register()

		try:
			await self.agent.telemetry_handler.log_agent_run()

			self.agent.logger.debug(
				f'🔧 Agent setup: Agent Session ID {self.agent.session_id[-4:]}, '
				f'Task ID {self.agent.task_id[-4:]}, '
				f'Browser Session ID {self.agent.browser_session.id[-4:] if self.agent.browser_session else "None"} '
				f'{"(connecting via CDP)" if (self.agent.browser_session and self.agent.browser_session.cdp_url) else "(launching local browser)"}'
			)

			self.agent._session_start_time = time.time()
			self.agent._task_start_time = self.agent._session_start_time

			if not self.agent.state.session_initialized:
				await self._initialize_session()

			if self.agent.enable_cloud_sync:
				self.agent.logger.debug('📡 Dispatching CreateAgentTaskEvent...')
				self.agent.eventbus.dispatch(CreateAgentTaskEvent.from_agent(self.agent))

			self.agent.telemetry_handler.log_first_step_startup()
			await self.agent.browser_session.start()

			try:
				await self.agent.step_executor.execute_initial_actions()
			except InterruptedError:
				pass

			self.agent.logger.debug(f'🔄 Starting main execution loop with max {max_steps} steps...')
			for step in range(max_steps):
				await self._handle_pause(signal_handler, step)

				if self._should_stop_for_failures():
					agent_run_error = f'Stopped due to {self.agent.settings.max_failures} consecutive failures'
					break

				if self.agent.state.stopped:
					self.agent.logger.info('🛑 Agent stopped')
					agent_run_error = 'Agent stopped programmatically'
					break

				if on_step_start is not None:
					await on_step_start(self.agent)

				self.agent.logger.debug(f'🚶 Starting step {step + 1}/{max_steps}...')
				step_info = AgentStepInfo(step_number=step, max_steps=max_steps)

				try:
					await asyncio.wait_for(
						self.agent.step(step_info),
						timeout=self.agent.settings.step_timeout,
					)
					self.agent.logger.debug(f'✅ Completed step {step + 1}/{max_steps}')
				except TimeoutError:
					error_msg = f'Step {step + 1} timed out after {self.agent.settings.step_timeout} seconds'
					self.agent.logger.error(f'⏰ {error_msg}')
					self.agent.state.consecutive_failures += 1
					self.agent.state.last_result = [ActionResult(error=error_msg)]

				if on_step_end is not None:
					await on_step_end(self.agent)

				if self.agent.history.is_done():
					await self._handle_completion()
					break
			else:
				agent_run_error = 'Failed to complete task in maximum steps'

				self.agent.history.add_item(
					AgentHistory(
						model_output=None,
						result=[ActionResult(error=agent_run_error, include_in_memory=True)],
						state=BrowserStateHistory(
							url='',
							title='',
							tabs=[],
							interacted_element=[],
							screenshot_path=None,
						),
						metadata=None,
					)
				)

				self.agent.logger.info(f'❌ {agent_run_error}')

			self.agent.logger.debug('📊 Collecting usage summary...')
			self.agent.history.usage = await self.agent.token_cost_service.get_usage_summary()

			if self.agent.history._output_model_schema is None and self.agent.output_model_schema is not None:
				self.agent.history._output_model_schema = self.agent.output_model_schema

			self.agent.logger.debug('🏁 Agent.run() completed successfully')
			return self.agent.history

		except KeyboardInterrupt:
			self.agent.logger.debug('Got KeyboardInterrupt during execution, returning current history')
			agent_run_error = 'KeyboardInterrupt'

			self.agent.history.usage = await self.agent.token_cost_service.get_usage_summary()

			return self.agent.history
		except Exception as exc:
			self.agent.logger.error(f'Agent run failed with exception: {exc}', exc_info=True)
			agent_run_error = str(exc)
			raise
		finally:
			await self.agent.token_cost_service.log_usage_summary()
			await self._cleanup(signal_handler, max_steps, agent_run_error)

	async def _initialize_session(self) -> None:
		if self.agent.enable_cloud_sync:
			self.agent.logger.debug('📡 Dispatching CreateAgentSessionEvent...')
			self.agent.eventbus.dispatch(CreateAgentSessionEvent.from_agent(self.agent))
			await asyncio.sleep(0.2)

		self.agent.state.session_initialized = True

	def _should_stop_for_failures(self) -> bool:
		if (
			self.agent.state.consecutive_failures
			>= self.agent.settings.max_failures + int(self.agent.settings.final_response_after_failure)
		):
			self.agent.logger.error(f'❌ Stopping due to {self.agent.settings.max_failures} consecutive failures')
			return True
		return False

	async def _handle_pause(self, signal_handler: SignalHandler, step: int) -> None:
		if self.agent.state.paused:
			self.agent.logger.debug(f'⏸️ Step {step}: Agent paused, waiting to resume...')
			await self.agent.pause_controller.wait_if_paused()
			signal_handler.reset()

	async def _handle_completion(self) -> None:
		self.agent.logger.debug('🎯 Task completed!')
		self.agent.telemetry_handler.log_completion()

		if self.agent.register_done_callback:
			if inspect.iscoroutinefunction(self.agent.register_done_callback):
				await self.agent.register_done_callback(self.agent.history)
			else:
				self.agent.register_done_callback(self.agent.history)

	async def _cleanup(self, signal_handler: SignalHandler, max_steps: int, agent_run_error: str | None) -> None:
		signal_handler.unregister()

		if not self._force_exit_telemetry_logged:
			try:
				self.agent.telemetry_handler.log_agent_event(max_steps=max_steps, agent_run_error=agent_run_error)
			except Exception as exc:
				self.agent.logger.error(f'Failed to log telemetry event: {exc}', exc_info=True)
		else:
			self.agent.logger.debug('Telemetry for force exit (SIGINT) was logged by custom exit callback.')

		if self.agent.enable_cloud_sync:
			self.agent.eventbus.dispatch(UpdateAgentTaskEvent.from_agent(self.agent))

		if self.agent.settings.generate_gif:
			output_path: str = 'agent_history.gif'
			if isinstance(self.agent.settings.generate_gif, str):
				output_path = self.agent.settings.generate_gif

			from browser_use.agent.gif import create_history_gif

			create_history_gif(task=self.agent.task, history=self.agent.history, output_path=output_path)

			if Path(output_path).exists():  # type: ignore[name-defined]
				output_event = await CreateAgentOutputFileEvent.from_agent_and_file(self.agent, output_path)
				self.agent.eventbus.dispatch(output_event)

		if self.agent.enable_cloud_sync and getattr(self.agent, 'cloud_sync', None) is not None:
			if self.agent.cloud_sync.auth_task and not self.agent.cloud_sync.auth_task.done():
				try:
					await asyncio.wait_for(self.agent.cloud_sync.auth_task, timeout=1.0)
				except TimeoutError:
					self.agent.logger.debug('Cloud authentication started - continuing in background')
				except Exception as exc:
					self.agent.logger.debug(f'Cloud authentication error: {exc}')

		await self.agent.eventbus.stop(timeout=3.0)
		await self.agent.close()


if TYPE_CHECKING:
	from browser_use.agent.service import Agent
