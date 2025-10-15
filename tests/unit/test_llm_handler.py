import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from browser_use.agent.llm_handler import LLMHandler
from browser_use.agent.views import AgentStepInfo
from browser_use.llm.messages import AssistantMessage, ContentPartTextParam, UserMessage


class DummyModelOutput:
	def __init__(self, actions: list | None = None, thinking: str | None = None):
		self.action = actions or []
		self.thinking = thinking
		self.evaluation_previous_goal = None
		self.memory = None
		self.next_goal = None


class DummyLLMResponse:
	def __init__(self, completion):
		self.completion = completion


@pytest.mark.asyncio
async def test_get_model_output_with_retry_inserts_safe_noop(dummy_action_model_class):
	agent = SimpleNamespace()
	agent.ActionModel = dummy_action_model_class
	agent.AgentOutput = 'AgentOutput'
	agent.state = SimpleNamespace(n_steps=1, last_model_output=None)
	agent.logger = MagicMock()
	agent.telemetry_handler = MagicMock()
	agent.telemetry_handler.log_model_response = MagicMock()
	agent.telemetry_handler.log_next_action_summary = MagicMock()
	agent.settings = SimpleNamespace(max_actions_per_step=5)
	agent.llm = SimpleNamespace(
		ainvoke=AsyncMock(
			side_effect=[
				DummyLLMResponse(DummyModelOutput([])),
				DummyLLMResponse(DummyModelOutput([])),
			]
		)
	)

	handler = LLMHandler(agent)

	result = await handler.get_model_output_with_retry([])

	assert len(result.action) == 1
	dump = result.action[0].model_dump()
	assert 'done' in dump
	assert dump['done']['text'] == 'No next action returned by LLM!'


@pytest.mark.asyncio
async def test_handle_post_llm_processing_invokes_callback(tmp_path: Path, dummy_action_model_class, monkeypatch):
	conversation_dir = tmp_path / 'conversations'
	conversation_dir.mkdir()

	agent = SimpleNamespace()
	agent.logger = MagicMock()
	agent.id = 'agent-id'
	agent.state = SimpleNamespace(
		last_model_output=SimpleNamespace(
			action=[dummy_action_model_class()],
			model_dump_json=lambda exclude_unset=True: '{}',
		),
		n_steps=1,
	)
	agent.register_new_step_callback = AsyncMock()
	agent.settings = SimpleNamespace(save_conversation_path=conversation_dir, save_conversation_path_encoding='utf-8')

	handler = LLMHandler(agent)
	mock_save = AsyncMock()
	monkeypatch.setattr('browser_use.agent.llm_handler.service.save_conversation', mock_save)

	browser_state = SimpleNamespace()
	messages = [UserMessage(content='hello')]

	await handler.handle_post_llm_processing(browser_state, messages)

	agent.register_new_step_callback.assert_awaited_once()
	target_path = conversation_dir / f'conversation_{agent.id}_{agent.state.n_steps}.txt'
	mock_save.assert_awaited_with(messages, agent.state.last_model_output, target_path, 'utf-8')


def test_process_messages_and_shorten_urls_returns_mapping(dummy_action_model_class):
	long_url = 'https://example.com/page?' + 'a' * 200

	agent = SimpleNamespace()
	agent._url_shortening_limit = 25
	handler = LLMHandler(agent)

	user_msg = UserMessage(content=f'Check {long_url}')
	assistant_msg = AssistantMessage(content=[ContentPartTextParam(text=f'Result {long_url}')])

	mapping = handler.shorten_urls_in_messages([user_msg, assistant_msg])

	assert len(mapping) == 1
	shortened = next(iter(mapping))
	assert shortened in user_msg.content
	assert shortened in assistant_msg.content[0].text
	assert mapping[shortened] == long_url
