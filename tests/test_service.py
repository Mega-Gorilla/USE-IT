import os
import sys
from unittest.mock import MagicMock, Mock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel

from browser_use.agent.service import Agent
from browser_use.browser.browser import Browser
from browser_use.browser.context import BrowserContext
from browser_use.controller.registry.service import Registry
from browser_use.controller.registry.views import ActionModel
from browser_use.controller.service import Controller


# run test with:
# python -m pytest tests/test_service.py
class TestAgent:
	@pytest.fixture
	def mock_controller(self):
		controller = Mock(spec=Controller)
		registry = Mock(spec=Registry)
		registry.registry = MagicMock()
		registry.registry.actions = {'test_action': MagicMock(param_model=MagicMock())}  # type: ignore
		controller.registry = registry
		return controller

	@pytest.fixture
	def mock_llm(self):
		return Mock(spec=BaseChatModel)

	@pytest.fixture
	def mock_browser(self):
		return Mock(spec=Browser)

	@pytest.fixture
	def mock_browser_context(self):
		return Mock(spec=BrowserContext)

	def test_convert_initial_actions(self, mock_controller, mock_llm, mock_browser, mock_browser_context):  # type: ignore
		"""
		Test that the _convert_initial_actions method correctly converts
		dictionary-based actions to ActionModel instances.

		This test ensures that:
		1. The method processes the initial actions correctly.
		2. The correct param_model is called with the right parameters.
		3. The ActionModel is created with the validated parameters.
		4. The method returns a list of ActionModel instances.
		"""
		# Arrange
		agent = Agent(
			task='Test task', llm=mock_llm, controller=mock_controller, browser=mock_browser, browser_context=mock_browser_context
		)
		initial_actions = [{'test_action': {'param1': 'value1', 'param2': 'value2'}}]

		# Mock the ActionModel
		mock_action_model = MagicMock(spec=ActionModel)
		mock_action_model_instance = MagicMock()
		mock_action_model.return_value = mock_action_model_instance
		agent.ActionModel = mock_action_model  # type: ignore

		# Act
		result = agent._convert_initial_actions(initial_actions)

		# Assert
		assert len(result) == 1
		mock_controller.registry.registry.actions['test_action'].param_model.assert_called_once_with(  # type: ignore
			param1='value1', param2='value2'
		)
		mock_action_model.assert_called_once()
		assert isinstance(result[0], MagicMock)
		assert result[0] == mock_action_model_instance

		# Check that the ActionModel was called with the correct parameters
		call_args = mock_action_model.call_args[1]
		assert 'test_action' in call_args
		assert call_args['test_action'] == mock_controller.registry.registry.actions['test_action'].param_model.return_value  # type: ignore
