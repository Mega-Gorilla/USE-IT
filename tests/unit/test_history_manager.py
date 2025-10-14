from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from browser_use.agent.history_manager import HistoryManager
from browser_use.agent.views import ActionResult, AgentHistory, AgentHistoryList, StepMetadata


@pytest.mark.asyncio
async def test_create_history_item_stores_screenshot(monkeypatch, dummy_action_model_class):
	class DummyAgentHistory(SimpleNamespace):
		@staticmethod
		def get_interacted_element(model_output, selector_map):
			return [None]

	monkeypatch.setattr('browser_use.agent.history_manager.service.AgentHistory', DummyAgentHistory)

	agent = SimpleNamespace()
	agent.state = SimpleNamespace(n_steps=2)
	agent.screenshot_service = SimpleNamespace(store_screenshot=AsyncMock(return_value='/tmp/screenshot.png'))
	agent.history = MagicMock()
	agent.logger = MagicMock()

	manager = HistoryManager(agent)

	model_output = SimpleNamespace(action=[dummy_action_model_class()], current_state=SimpleNamespace(next_goal='goal'))
	browser_state = SimpleNamespace(
		url='http://example.com',
		title='Example',
		tabs=[],
		screenshot='raw-bytes',
		dom_state=SimpleNamespace(
			selector_map={'1': SimpleNamespace(parent_branch_hash=lambda: 'hash')}
		),
	)

	await manager.create_history_item(
		model_output=model_output,
		browser_state_summary=browser_state,
		result=[ActionResult(extracted_content='ok')],
		metadata=StepMetadata(step_number=2, step_start_time=0, step_end_time=1),
		state_message='state',
	)

	agent.screenshot_service.store_screenshot.assert_awaited_once()
	assert agent.history.add_item.called


@pytest.mark.asyncio
async def test_add_initial_actions_history_adds_entry(monkeypatch, dummy_action_model_class):
	class DummyAgentHistory(SimpleNamespace):
		@staticmethod
		def get_interacted_element(model_output, selector_map):
			return [None]

	monkeypatch.setattr('browser_use.agent.history_manager.service.AgentHistory', DummyAgentHistory)

	agent = SimpleNamespace()
	agent.logger = MagicMock()
	agent.initial_url = 'http://example.com'
	agent.initial_actions = [dummy_action_model_class(navigate={'url': 'http://example.com'})]
	agent.state = SimpleNamespace(last_result=[ActionResult(extracted_content='init')])
	agent.history = MagicMock()
	agent.AgentOutput = lambda **kwargs: SimpleNamespace(action=agent.initial_actions)

	manager = HistoryManager(agent)

	await manager.add_initial_actions_history()

	assert agent.history.add_item.called


@pytest.mark.asyncio
async def test_rerun_history_raises_after_retries(dummy_action_model_class):
	history_item = SimpleNamespace(
		model_output=SimpleNamespace(
			action=[dummy_action_model_class(click={'selector': '#id'})],
			current_state=SimpleNamespace(next_goal='goal'),
		),
		result=[ActionResult(extracted_content='before')],
		state=SimpleNamespace(interacted_element=[None]),
		metadata=SimpleNamespace(step_number=1),
	)
	history = SimpleNamespace(history=[history_item])

	agent = SimpleNamespace()
	agent.logger = MagicMock()
	agent.browser_session = SimpleNamespace(start=AsyncMock(), get_browser_state_summary=AsyncMock(), kill=AsyncMock())
	agent.state = SimpleNamespace(session_initialized=False)

	manager = HistoryManager(agent)
	manager._execute_history_step = AsyncMock(side_effect=RuntimeError('step failed'))

	with pytest.raises(RuntimeError):
		await manager.rerun_history(history, max_retries=2, skip_failures=False, delay_between_actions=0)

	assert agent.browser_session.start.await_count == 1
