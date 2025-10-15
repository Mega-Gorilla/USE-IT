import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from browser_use.agent.telemetry import TelemetryHandler
from browser_use.agent.views import ActionResult


def make_agent_for_telemetry():
	history_item = SimpleNamespace(
		model_output=SimpleNamespace(action=[SimpleNamespace(model_dump=lambda exclude_unset=True: {'navigate': {'url': 'http://example.com'}})]),
		result=[ActionResult(extracted_content='ok')],
		state=SimpleNamespace(url='http://example.com', title='Example'),
		metadata=SimpleNamespace(step_number=1),
	)
	history = SimpleNamespace(
		history=[history_item],
		errors=lambda: [],
		urls=lambda: ['http://example.com'],
		total_duration_seconds=lambda: 1.23,
		is_successful=lambda: True,
		final_result=lambda: 'done',
		number_of_steps=lambda: 1,
		usage=None,
	)

	agent = SimpleNamespace()
	agent.logger = logging.getLogger('browser_use.tests.unit.telemetry')
	agent.logger.setLevel(logging.DEBUG)
	agent.task = 'test task'
	agent.version = '0.0.0'
	agent.source = 'source'
	agent.history = history
	agent.telemetry = MagicMock()
	agent.llm = SimpleNamespace(model='mock-model', provider='mock')
	agent.settings = SimpleNamespace(
		max_actions_per_step=5,
		use_vision=True,
	)
	agent.browser_session = SimpleNamespace(cdp_url=None)
	agent.state = SimpleNamespace(n_steps=1)
	agent.token_cost_service = SimpleNamespace(
		get_usage_tokens_for_model=lambda model: SimpleNamespace(prompt_tokens=10)
	)
	return agent


def test_log_step_completion_summary_logs_status(caplog):
	agent = make_agent_for_telemetry()
	handler = TelemetryHandler(agent)

	with caplog.at_level('DEBUG'):
		handler.log_step_completion_summary(time.time() - 0.1, [ActionResult(extracted_content='ok')])

	assert any('Step 1' in msg for msg in caplog.messages)


def test_log_agent_event_captures_payload():
	agent = make_agent_for_telemetry()
	handler = TelemetryHandler(agent)

	handler.log_agent_event(max_steps=5, agent_run_error=None)

	assert agent.telemetry.capture.called
	event = agent.telemetry.capture.call_args.args[0]
	payload = event.__dict__
	assert payload['model'] == 'mock-model'
	assert payload['steps'] == 1
