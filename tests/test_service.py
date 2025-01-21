import pytest

from browser_use.agent.message_manager.service import MessageManager
from browser_use.agent.service import Agent
from browser_use.agent.views import ActionResult, AgentOutput
from browser_use.browser.views import BrowserState
from langchain_core.language_models.chat_models import BaseChatModel
from unittest.mock import AsyncMock, MagicMock, patch

class TestAgent:
    """Test class for Agent"""

    @pytest.mark.asyncio
    async def test_step_error_handling(self):
        """
        Test the error handling in the step method of the Agent class.
        This test simulates a failure in the get_next_action method and
        checks if the error is properly handled and recorded.
        """
        # Mock the LLM
        mock_llm = MagicMock(spec=BaseChatModel)

        # Mock the MessageManager
        with patch('browser_use.agent.service.MessageManager') as mock_message_manager:
            # Create an Agent instance with mocked dependencies
            agent = Agent(task="Test task", llm=mock_llm)

            # Mock the get_next_action method to raise an exception
            agent.get_next_action = AsyncMock(side_effect=ValueError("Test error"))

            # Mock the browser_context
            agent.browser_context = AsyncMock()
            agent.browser_context.get_state = AsyncMock(return_value=BrowserState(
                url="https://example.com",
                title="Example",
                element_tree=MagicMock(),  # Mocked element tree
                tabs=[],
                selector_map={},
                screenshot=""
            ))

            # Mock the controller
            agent.controller = AsyncMock()

            # Mock the telemetry
            agent.telemetry = MagicMock()

            # Call the step method
            await agent.step()

            # Assert that the error was handled and recorded
            assert agent.consecutive_failures == 1
            assert len(agent._last_result) == 1
            assert isinstance(agent._last_result[0], ActionResult)
            assert "Test error" in agent._last_result[0].error
            assert agent._last_result[0].include_in_memory == True

            # Assert that the telemetry was captured
            agent.telemetry.capture.assert_called_once()

    """Test class for Agent"""

    @pytest.mark.asyncio
    async def test_step_error_handling(self):
        """
        Test the error handling in the step method of the Agent class.
        This test simulates a failure in the get_next_action method and
        checks if the error is properly handled and recorded.
        """
        # Mock the LLM
        mock_llm = MagicMock(spec=BaseChatModel)

        # Mock the MessageManager
        with patch('browser_use.agent.service.MessageManager') as mock_message_manager_class:
            mock_message_manager = MagicMock()
            mock_message_manager_class.return_value = mock_message_manager

            # Mock the ProductTelemetry
            with patch('browser_use.agent.service.ProductTelemetry') as mock_telemetry_class:
                mock_telemetry = MagicMock()
                mock_telemetry_class.return_value = mock_telemetry

                # Create an Agent instance with mocked dependencies
                agent = Agent(task="Test task", llm=mock_llm)

                # Mock the get_next_action method to raise an exception
                agent.get_next_action = AsyncMock(side_effect=ValueError("Test error"))

                # Mock the browser_context
                agent.browser_context = AsyncMock()
                agent.browser_context.get_state = AsyncMock(return_value=BrowserState(
                    url="https://example.com",
                    title="Example",
                    element_tree=MagicMock(),  # Provide a mock element_tree
                    tabs=[],
                    selector_map={},
                    screenshot=""
                ))

                # Mock the controller
                agent.controller = AsyncMock()

                # Call the step method
                await agent.step()

                # Assert that the error was handled and recorded
                assert agent.consecutive_failures == 1
                assert len(agent._last_result) == 1
                assert isinstance(agent._last_result[0], ActionResult)
                assert "Test error" in agent._last_result[0].error
                assert agent._last_result[0].include_in_memory == True

                # Assert that the telemetry was captured
                mock_telemetry.capture.assert_called_once()