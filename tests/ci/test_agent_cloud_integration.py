"""
Tests for browser-use agent integration with cloud sync.
"""

import asyncio
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from browser_use.agent.service import Agent
from browser_use.sync.service import CloudSyncService
from tests.ci.mocks import create_mocked_browser, create_mocked_model


class TestAgentCloudIntegration:
	"""Test agent integration with cloud sync."""

	@pytest.fixture
	def mock_browser(self):
		"""Create mocked browser."""
		return create_mocked_browser()

	@pytest.fixture
	def mock_model(self):
		"""Create mocked LLM model."""
		return create_mocked_model()

	@pytest.fixture
	def mock_cloud_sync(self):
		"""Create mocked cloud sync service."""
		sync = Mock(spec=CloudSyncService)
		sync.send_event = AsyncMock()
		sync.authenticate = AsyncMock(return_value=True)
		sync._authenticated = True
		return sync

	@pytest.fixture
	def agent_with_cloud(self, mock_browser, mock_model, mock_cloud_sync):
		"""Create agent with cloud sync enabled."""
		with patch('browser_use.agent.service.CloudSyncService', return_value=mock_cloud_sync):
			with patch.dict(os.environ, {'BROWSERUSE_CLOUD_SYNC': 'true'}):
				agent = Agent(
					task='Test task',
					llm=mock_model,
					browser_context=mock_browser,
				)
				# Manually set cloud sync since we're mocking
				agent.cloud_sync = mock_cloud_sync
				return agent

	async def test_agent_emits_session_start_event(self, agent_with_cloud, mock_cloud_sync):
		"""Test that agent emits session start event."""
		# Run agent
		await agent_with_cloud.run()

		# Check that session start event was sent
		calls = mock_cloud_sync.send_event.call_args_list
		session_events = [call for call in calls if call[1]['event_type'] == 'CreateAgentSession']

		assert len(session_events) == 1
		event_data = session_events[0][1]['event_data']
		assert 'id' in event_data
		assert 'started_at' in event_data

	async def test_agent_emits_task_events(self, agent_with_cloud, mock_cloud_sync):
		"""Test that agent emits task events."""
		# Run agent
		await agent_with_cloud.run()

		# Check task events
		calls = mock_cloud_sync.send_event.call_args_list

		# Should have CreateAgentTask event
		create_task_events = [call for call in calls if call[1]['event_type'] == 'CreateAgentTask']
		assert len(create_task_events) == 1
		create_data = create_task_events[0][1]['event_data']
		assert create_data['task'] == 'Test task'
		assert create_data['status'] == 'running'
		assert 'agent_session_id' in create_data

		# Should have UpdateAgentTask event when done
		update_task_events = [call for call in calls if call[1]['event_type'] == 'UpdateAgentTask']
		assert len(update_task_events) >= 1
		final_update = update_task_events[-1][1]['event_data']
		assert final_update['status'] in ['completed', 'failed']
		assert 'completed_at' in final_update

	async def test_agent_emits_step_events(self, agent_with_cloud, mock_cloud_sync, mock_model):
		"""Test that agent emits step events."""
		# Configure model to return specific actions
		mock_model.generate_response.side_effect = [
			(Mock(action_type='click', coordinate=[100, 200]), 'Click button'),
			(Mock(action_type='done'), 'Task completed'),
		]

		# Run agent
		await agent_with_cloud.run()

		# Check step events
		calls = mock_cloud_sync.send_event.call_args_list

		# Should have CreateAgentStep events
		create_step_events = [call for call in calls if call[1]['event_type'] == 'CreateAgentStep']
		assert len(create_step_events) >= 1

		first_step = create_step_events[0][1]['event_data']
		assert first_step['step_number'] == 1
		assert first_step['type'] == 'click'
		assert 'started_at' in first_step

		# Should have UpdateAgentStep events
		update_step_events = [call for call in calls if call[1]['event_type'] == 'UpdateAgentStep']
		assert len(update_step_events) >= 1

		step_update = update_step_events[0][1]['event_data']
		assert step_update['status'] in ['completed', 'failed']
		assert 'completed_at' in step_update

	async def test_agent_with_pre_auth_flow(self, mock_browser, mock_model):
		"""Test agent with pre-authentication flow."""
		# Create mock cloud sync that starts unauthenticated
		mock_sync = Mock(spec=CloudSyncService)
		mock_sync.send_event = AsyncMock()
		mock_sync.authenticate = AsyncMock(return_value=True)
		mock_sync._authenticated = False

		with patch('browser_use.agent.service.CloudSyncService', return_value=mock_sync):
			with patch.dict(os.environ, {'BROWSERUSE_CLOUD_SYNC': 'true'}):
				agent = Agent(
					task='Test task',
					llm=mock_model,
					browser_context=mock_browser,
				)
				agent.cloud_sync = mock_sync

				# Run agent
				await agent.run()

				# Check that events were sent even without auth
				assert mock_sync.send_event.call_count > 0

				# Authenticate
				mock_sync._authenticated = True
				await mock_sync.authenticate()

				# Run another task
				agent.task = 'Second task'
				await agent.run()

				# Check that authentication was used
				assert mock_sync.authenticate.called

	async def test_cloud_sync_disabled(self, mock_browser, mock_model):
		"""Test that cloud sync can be disabled."""
		with patch.dict(os.environ, {'BROWSERUSE_CLOUD_SYNC': 'false'}):
			agent = Agent(
				task='Test task',
				llm=mock_model,
				browser_context=mock_browser,
			)

			assert agent.cloud_sync is None

			# Run agent - should work without cloud sync
			await agent.run()

	async def test_event_data_completeness(self, agent_with_cloud, mock_cloud_sync, mock_model):
		"""Test that events contain all required data."""
		# Configure model
		mock_model.generate_response.side_effect = [
			(Mock(action_type='type', text='Hello world'), 'Type greeting'),
			(Mock(action_type='done'), 'Task completed'),
		]

		# Run agent
		result = await agent_with_cloud.run()

		# Get all events
		calls = mock_cloud_sync.send_event.call_args_list

		# Check session event
		session_event = next(call for call in calls if call[1]['event_type'] == 'CreateAgentSession')
		session_data = session_event[1]['event_data']
		assert isinstance(session_data['id'], str)
		assert isinstance(session_data['started_at'], str)

		# Check task event
		task_event = next(call for call in calls if call[1]['event_type'] == 'CreateAgentTask')
		task_data = task_event[1]['event_data']
		assert task_data['task'] == 'Test task'
		assert task_data['status'] == 'running'
		assert task_data['agent_session_id'] == session_data['id']
		assert 'model' in task_data
		assert isinstance(task_data['started_at'], str)

		# Check step event
		step_event = next(call for call in calls if call[1]['event_type'] == 'CreateAgentStep')
		step_data = step_event[1]['event_data']
		assert step_data['type'] == 'type'
		assert step_data['step_number'] == 1
		assert 'action_data' in step_data
		assert step_data['agent_session_id'] == session_data['id']
		assert 'agent_task_id' in step_data
		assert isinstance(step_data['started_at'], str)

	async def test_error_handling_continues(self, agent_with_cloud, mock_cloud_sync):
		"""Test that agent continues even if cloud sync fails."""
		# Make cloud sync fail
		mock_cloud_sync.send_event.side_effect = Exception('Network error')

		# Run agent - should not raise
		result = await agent_with_cloud.run()

		# Agent should complete successfully
		assert result is not None
		assert mock_cloud_sync.send_event.call_count > 0

	async def test_eventbus_integration(self, agent_with_cloud, mock_cloud_sync):
		"""Test EventBus integration with cloud sync."""
		from bubus import Event

		# Access agent's event bus
		event_bus = agent_with_cloud._event_bus

		# Emit custom event
		custom_event = Event(type='test_event', data={'test': 'data'})
		await event_bus.emit(custom_event)

		# Process any pending events
		await asyncio.sleep(0.1)

		# Cloud sync should handle AgentEvent types
		# Check that only expected events were sent to cloud
		calls = mock_cloud_sync.send_event.call_args_list
		event_types = [call[1]['event_type'] for call in calls]

		# Should have session and task events, but not the custom test event
		assert 'CreateAgentSession' in event_types
		assert 'CreateAgentTask' in event_types
		assert 'test_event' not in event_types  # Custom events not synced

	async def test_session_id_persistence(self, mock_browser, mock_model):
		"""Test that agent session ID persists across runs."""
		mock_sync = Mock(spec=CloudSyncService)
		mock_sync.send_event = AsyncMock()
		mock_sync._authenticated = True

		with patch('browser_use.agent.service.CloudSyncService', return_value=mock_sync):
			with patch.dict(os.environ, {'BROWSERUSE_CLOUD_SYNC': 'true'}):
				# Create first agent
				agent1 = Agent(
					task='First task',
					llm=mock_model,
					browser_context=mock_browser,
				)
				agent1.cloud_sync = mock_sync

				# Run first agent
				await agent1.run()

				# Get session ID from first run
				session_calls = [
					call for call in mock_sync.send_event.call_args_list if call[1]['event_type'] == 'CreateAgentSession'
				]
				session_id_1 = session_calls[0][1]['event_data']['id']

				# Create second agent with same session
				agent2 = Agent(
					task='Second task',
					llm=mock_model,
					browser_context=mock_browser,
					agent_id=session_id_1,  # Reuse session ID
				)
				agent2.cloud_sync = mock_sync

				# Clear previous calls
				mock_sync.send_event.reset_mock()

				# Run second agent
				await agent2.run()

				# Should not create new session, just new task
				session_calls_2 = [
					call for call in mock_sync.send_event.call_args_list if call[1]['event_type'] == 'CreateAgentSession'
				]
				assert len(session_calls_2) == 0  # No new session

				# But should create new task with same session ID
				task_calls = [call for call in mock_sync.send_event.call_args_list if call[1]['event_type'] == 'CreateAgentTask']
				assert len(task_calls) == 1
				assert task_calls[0][1]['event_data']['agent_session_id'] == session_id_1
