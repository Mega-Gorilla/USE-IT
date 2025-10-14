import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from browser_use.agent.step_executor import StepExecutor
from browser_use.agent.views import ActionResult, AgentStepInfo


def make_browser_state(url: str = 'http://example.com', screenshot: str | None = None):
	return SimpleNamespace(
		url=url,
		title='Example',
		tabs=[],
		screenshot=screenshot,
		dom_state=SimpleNamespace(selector_map={}),
	)


def build_agent(test_logger):
	state = SimpleNamespace(
		n_steps=1,
		last_model_output=None,
		last_result=None,
		consecutive_failures=0,
	)

	browser_session = SimpleNamespace()
	browser_session.get_browser_state_summary = AsyncMock(return_value=make_browser_state())
	browser_session._cached_browser_state_summary = None

	telemetry_handler = MagicMock()
	telemetry_handler.log_step_context = MagicMock()
	telemetry_handler.log_step_completion_summary = MagicMock()
	telemetry_handler.log_model_response = MagicMock()
	telemetry_handler.log_next_action_summary = MagicMock()
	telemetry_handler.log_completion = MagicMock()

	message_manager = MagicMock()
	message_manager.create_state_messages = MagicMock()
	message_manager._add_context_message = MagicMock()
	message_manager.last_state_message_text = 'state'

	agent = SimpleNamespace(
		state=state,
		browser_session=browser_session,
		include_recent_events=False,
		telemetry_handler=telemetry_handler,
		_message_manager=message_manager,
		tools=SimpleNamespace(registry=MagicMock(get_prompt_description=MagicMock(return_value='desc'))),
		sensitive_data=None,
		available_file_paths=['/tmp/download.txt'],
		file_system=None,
		settings=SimpleNamespace(
			use_vision=True,
			max_failures=3,
			final_response_after_failure=True,
			llm_timeout=30,
			max_actions_per_step=5,
			flash_mode=False,
			page_extraction_llm=None,
		),
		logger=test_logger,
		_check_and_update_downloads=AsyncMock(),
		_check_stop_or_pause=AsyncMock(),
		_update_action_models_for_page=AsyncMock(),
		browser_profile=SimpleNamespace(wait_between_actions=0),
		AgentOutput='AgentOutput',
		DoneAgentOutput='DoneAgentOutput',
		tools_act_calls=[],
		tools_act_results=[],
	)

	def _tools_act(**kwargs):
		agent.tools_act_calls.append(kwargs)
		return asyncio.Future()

	agent.tools.act = AsyncMock()
	return agent


@pytest.mark.asyncio
async def test_prepare_context_sets_up_messages(test_logger):
	agent = build_agent(test_logger)
	executor = StepExecutor(agent)

	summary = await executor.prepare_context(step_info=None)

	agent._check_and_update_downloads.assert_awaited_with('Step 1: after getting browser state')
	agent._update_action_models_for_page.assert_awaited_with(summary.url)
	agent.telemetry_handler.log_step_context.assert_called_once()
	agent._message_manager.create_state_messages.assert_called_once()
	assert summary.url == 'http://example.com'


@pytest.mark.asyncio
async def test_handle_step_error_records_failure(test_logger):
	agent = build_agent(test_logger)
	executor = StepExecutor(agent)

	await executor.handle_step_error(RuntimeError('boom'))

	assert agent.state.consecutive_failures == 1
	assert agent.state.last_result
	assert 'boom' in agent.state.last_result[0].error


@pytest.mark.asyncio
async def test_force_done_after_last_step_switches_output(test_logger):
	agent = build_agent(test_logger)
	executor = StepExecutor(agent)

	step_info = AgentStepInfo(step_number=4, max_steps=5)
	await executor.force_done_after_last_step(step_info)

	agent._message_manager._add_context_message.assert_called_once()
	assert agent.AgentOutput == agent.DoneAgentOutput


@pytest.mark.asyncio
async def test_multi_act_stops_on_done_action(test_logger, dummy_action_model_class):
	agent = build_agent(test_logger)
	executor = StepExecutor(agent)

	action_call = ActionResult(is_done=True, success=True, extracted_content='completed')
	agent.tools.act = AsyncMock(return_value=action_call)

	actions = [
		dummy_action_model_class(click={'selector': '#blue'}),
		dummy_action_model_class(done={'success': True}),
	]

	results = await executor.multi_act(actions)

	assert len(results) == 1
	agent.tools.act.assert_awaited_once()
	agent._check_stop_or_pause.assert_awaited()
