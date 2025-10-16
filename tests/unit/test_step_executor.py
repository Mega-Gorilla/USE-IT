import asyncio
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from browser_use.agent.step_executor import StepExecutor
from browser_use.llm.exceptions import ModelProviderError
from browser_use.filesystem.file_system import FileSystem
from browser_use.agent.views import ActionResult, AgentStepInfo, ApprovalResult


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

	file_system = FileSystem(Path(tempfile.mkdtemp()))
	agent = SimpleNamespace(
		state=state,
		browser_session=browser_session,
		include_recent_events=False,
		telemetry_handler=telemetry_handler,
		_message_manager=message_manager,
		tools=SimpleNamespace(registry=MagicMock(get_prompt_description=MagicMock(return_value='desc'))),
		sensitive_data=None,
		available_file_paths=['/tmp/download.txt'],
		file_system=file_system,
		settings=SimpleNamespace(
			use_vision=True,
			max_failures=3,
			final_response_after_failure=True,
			llm_timeout=30,
			max_actions_per_step=5,
			flash_mode=False,
			page_extraction_llm=None,
			interactive_mode=False,
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
	agent.llm = SimpleNamespace(model='mock-model')

	def _tools_act(**kwargs):
		agent.tools_act_calls.append(kwargs)
		return asyncio.Future()

	agent.tools.act = AsyncMock()
	agent.llm_handler = SimpleNamespace(
		get_model_output_with_retry=AsyncMock(return_value=SimpleNamespace(action=[])),
		handle_post_llm_processing=AsyncMock(),
	)
	agent.history = SimpleNamespace(
		history=[],
		add_item=MagicMock(),
		is_done=lambda: False,
		errors=lambda: [],
		urls=lambda: [],
		total_duration_seconds=lambda: 0,
		final_result=lambda: None,
		usage=None,
)
	agent.history_manager = SimpleNamespace(create_history_item=AsyncMock())
	agent.save_file_system_state = MagicMock()
	agent.enable_cloud_sync = False
	agent.approval_callback = None
	agent.stop = MagicMock()
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
async def test_handle_step_error_model_provider_shows_user_message(test_logger, caplog):
	agent = build_agent(test_logger)
	executor = StepExecutor(agent)

	error = ModelProviderError(
		"503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.', 'status': 'UNAVAILABLE'}}",
		status_code=503,
		model='gemini-flash-latest',
	)

	with caplog.at_level(logging.DEBUG, logger='browser_use.tests.unit'):
		await executor.handle_step_error(error)

	assert agent.state.consecutive_failures == 1
	assert agent.state.last_result
	user_message = agent.state.last_result[0].error
	assert 'provider is overloaded' in user_message
	assert '{' not in user_message
	assert 'Stacktrace' not in user_message

	error_logs = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.ERROR]
	assert any('503 Service Unavailable' in msg for msg in error_logs)

	debug_logs = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.DEBUG]
	assert any('Model provider error details' in msg for msg in debug_logs)


@pytest.mark.asyncio
async def test_handle_step_error_rate_limit_message(test_logger, caplog):
	agent = build_agent(test_logger)
	executor = StepExecutor(agent)

	error = ModelProviderError(
		'429 Too Many Requests',
		status_code=429,
		model='mock-model',
	)

	with caplog.at_level(logging.DEBUG, logger='browser_use.tests.unit'):
		await executor.handle_step_error(error)

	user_message = agent.state.last_result[0].error
	assert 'rate limit' in user_message.lower()
	assert 'retry' in user_message.lower()
	assert '{' not in user_message

	error_logs = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.ERROR]
	assert any('429 Too Many Requests' in msg for msg in error_logs)


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


@pytest.mark.asyncio
async def test_execute_step_handles_model_provider_error(test_logger):
	agent = build_agent(test_logger)
	error = ModelProviderError('503 UNAVAILABLE')
	agent.llm_handler.get_model_output_with_retry = AsyncMock(side_effect=error)

	executor = StepExecutor(agent)

	await executor.execute_step(step_info=None)

	assert agent.state.consecutive_failures == 1
	assert agent.state.last_result
	assert 'provider returned an internal error' in agent.state.last_result[0].error
	assert '{' not in agent.state.last_result[0].error


@pytest.mark.asyncio
async def test_execute_step_interactive_skip(test_logger, dummy_action_model_class):
	agent = build_agent(test_logger)
	agent.settings.interactive_mode = True
	action = dummy_action_model_class(click={'selector': '#submit'})
	model_output = SimpleNamespace(action=[action])
	agent.llm_handler.get_model_output_with_retry = AsyncMock(return_value=model_output)
	agent.approval_callback = MagicMock(return_value=(False, None))

	executor = StepExecutor(agent)

	await executor.execute_step(step_info=None)

	agent.tools.act.assert_not_called()
	agent.stop.assert_not_called()
	assert agent.state.last_result
	assert 'skipped execution' in agent.state.last_result[0].long_term_memory
	agent.approval_callback.assert_called_once()


@pytest.mark.asyncio
async def test_execute_step_interactive_retry_then_approve(test_logger, dummy_action_model_class):
	agent = build_agent(test_logger)
	agent.settings.interactive_mode = True
	first_action = dummy_action_model_class(click={'selector': '#one'})
	second_action = dummy_action_model_class(click={'selector': '#two'})
	first_output = SimpleNamespace(action=[first_action])
	second_output = SimpleNamespace(action=[second_action])
	agent.llm_handler.get_model_output_with_retry = AsyncMock(side_effect=[first_output, second_output])
	agent.approval_callback = MagicMock(
		side_effect=[
			ApprovalResult(decision='retry', feedback='Use index #two'),
			ApprovalResult(decision='approve'),
		]
	)
	agent.tools.act = AsyncMock(return_value=ActionResult(is_done=True, success=True, extracted_content='done'))

	executor = StepExecutor(agent)

	await executor.execute_step(step_info=None)

	assert agent.approval_callback.call_count == 2
	assert agent.llm_handler.get_model_output_with_retry.await_count == 2
	agent._message_manager._add_context_message.assert_called()
	message_arg = agent._message_manager._add_context_message.call_args[0][0]
	assert '<human_feedback>Use index #two</human_feedback>' in message_arg.content
	agent.tools.act.assert_awaited()
	agent.stop.assert_not_called()


@pytest.mark.asyncio
async def test_request_human_approval_requires_callback(test_logger, dummy_action_model_class):
	agent = build_agent(test_logger)
	agent.settings.interactive_mode = True
	agent.state.last_model_output = SimpleNamespace(action=[dummy_action_model_class(click={'selector': '#ok'})])
	executor = StepExecutor(agent)

	with pytest.raises(RuntimeError) as exc:
		await executor.request_human_approval(step_info=None, browser_state_summary=make_browser_state())

	assert 'approval_callback' in str(exc.value)
