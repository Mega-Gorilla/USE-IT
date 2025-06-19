"""
Tests for OAuth2 device flow and cloud sync functionality.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiofiles
import httpx
import pytest
from pytest_httpserver import HTTPServer

from browser_use.sync.auth import TEMP_USER_ID, DeviceAuthClient
from browser_use.sync.service import CloudSyncService


class MockResponse:
	"""Mock HTTP response for testing."""

	def __init__(self, json_data, status=200):
		self.json_data = json_data
		self.status = status

	async def json(self):
		return self.json_data


@pytest.fixture
def mock_httpx_client():
	"""Mock httpx AsyncClient."""
	return patch('httpx.AsyncClient')


# Define config dir for tests
# BROWSER_USE_CONFIG_DIR = Path.home() / ".config" / "browseruse"
BROWSER_USE_CONFIG_DIR = Path(tempfile.mkdtemp()) / '.config' / 'browseruse'


@pytest.fixture
def temp_config_dir():
	"""Create temporary config directory."""
	with tempfile.TemporaryDirectory() as tmpdir:
		temp_dir = Path(tmpdir) / '.config' / 'browseruse'
		temp_dir.mkdir(parents=True, exist_ok=True)

		# Temporarily replace the config dir
		original = BROWSER_USE_CONFIG_DIR
		import browser_use.sync.auth
		import browser_use.utils

		browser_use.sync.auth.BROWSER_USE_CONFIG_DIR = temp_dir
		browser_use.utils.BROWSER_USE_CONFIG_DIR = temp_dir

		yield temp_dir

		# Restore original
		browser_use.sync.auth.BROWSER_USE_CONFIG_DIR = original
		browser_use.utils.BROWSER_USE_CONFIG_DIR = original


@pytest.fixture
async def http_client(httpserver: HTTPServer):
	"""Create a real HTTP client pointed at the test server"""
	async with httpx.AsyncClient(base_url=httpserver.url_for('')) as client:
		yield client


class TestDeviceAuthClient:
	"""Test DeviceAuthClient class."""

	async def test_init_creates_config_dir(self, temp_config_dir):
		"""Test that initialization creates config directory."""
		auth = DeviceAuthClient()
		assert temp_config_dir.exists()
		assert (temp_config_dir / 'cloud_auth.json').exists() is False

	async def test_load_credentials_no_file(self, temp_config_dir):
		"""Test loading credentials when file doesn't exist."""
		auth = DeviceAuthClient()
		# When no file exists, auth_config should have no token/user_id
		assert auth.auth_config.api_token is None
		assert auth.auth_config.user_id is None
		assert not auth.is_authenticated

	async def test_save_and_load_credentials(self, temp_config_dir):
		"""Test saving and loading credentials."""
		auth = DeviceAuthClient()

		# Update auth config and save
		auth.auth_config.api_token = 'test-key-123'
		auth.auth_config.user_id = 'test-user-123'
		auth.auth_config.authorized_at = datetime.utcnow()
		auth.auth_config.save_to_file()

		# Load in a new instance
		auth2 = DeviceAuthClient()
		assert auth2.auth_config.api_token == 'test-key-123'
		assert auth2.auth_config.user_id == 'test-user-123'
		assert auth2.is_authenticated
		assert (temp_config_dir / 'cloud_auth.json').exists()

		# Check file permissions (should be readable only by owner)
		stat = (temp_config_dir / 'cloud_auth.json').stat()
		assert oct(stat.st_mode)[-3:] == '600'

	async def test_is_authenticated(self, temp_config_dir):
		"""Test authentication status check."""
		auth = DeviceAuthClient()

		# Not authenticated initially
		assert auth.is_authenticated is False

		# Save credentials
		auth.auth_config.api_token = 'test-key'
		auth.auth_config.user_id = 'test-user'
		auth.auth_config.save_to_file()

		# Reload to verify persistence
		auth2 = DeviceAuthClient()
		assert auth2.is_authenticated is True

	async def test_get_credentials(self, temp_config_dir):
		"""Test getting credentials."""
		auth = DeviceAuthClient()

		# No credentials initially
		assert auth.api_token is None
		assert auth.user_id == TEMP_USER_ID  # Should return temp user ID when not authenticated

		# Save credentials
		auth.auth_config.api_token = 'test-key'
		auth.auth_config.user_id = 'test-user'

		# Get credentials
		assert auth.api_token == 'test-key'
		assert auth.user_id == 'test-user'

	async def test_start_device_flow(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test starting device flow."""
		# Set up the test server response
		httpserver.expect_request(
			'/api/v1/oauth/device/authorize',
			method='POST',
		).respond_with_json(
			{
				'device_code': 'test-device-code',
				'user_code': 'ABCD-1234',
				'verification_uri': 'https://example.com/device',
				'verification_uri_complete': 'https://example.com/device?user_code=ABCD-1234',
				'expires_in': 1800,
				'interval': 5,
			}
		)

		# Create auth client with injected http client
		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)
		result = await auth.start_device_authorization('test-session-id')

		assert result['device_code'] == 'test-device-code'
		assert result['user_code'] == 'ABCD-1234'
		assert 'verification_uri' in result

		# Verify the request was made correctly
		request = httpserver.log[0][0]
		assert request.method == 'POST'
		# Get the body as string
		body = request.get_data(as_text=True)
		assert 'client_id=bu_cli' in body
		assert 'agent_session_id=test-session-id' in body

	async def test_poll_for_token_pending(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test polling when authorization is pending."""
		# Set up the test server to always return pending
		httpserver.expect_request(
			'/api/v1/oauth/device/token',
			method='POST',
		).respond_with_json(
			{
				'error': 'authorization_pending',
				'error_description': 'Authorization pending',
			}
		)

		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)
		# Use very short timeout to avoid long test
		result = await auth.poll_for_token('test-device-code', interval=0.1, timeout=0.5)

		assert result is None
		assert not auth.is_authenticated

	async def test_poll_for_token_success(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test successful token polling."""
		# Set up the test server to return success immediately
		httpserver.expect_request(
			'/api/v1/oauth/device/token',
			method='POST',
		).respond_with_json(
			{
				'access_token': 'test-api-key',
				'token_type': 'Bearer',
				'user_id': 'test-user-123',
				'scope': 'read write',
			}
		)

		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)
		result = await auth.poll_for_token('test-device-code')

		assert result is not None
		assert result['access_token'] == 'test-api-key'
		assert result['user_id'] == 'test-user-123'

	async def test_wait_for_authorization(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test waiting for authorization with polling."""
		# Track number of requests
		request_count = 0

		def handle_token_request(request):
			nonlocal request_count
			request_count += 1

			from werkzeug.wrappers import Response

			if request_count < 3:
				# First two requests return pending
				return Response(
					json.dumps({'error': 'authorization_pending', 'error_description': 'Authorization pending'}),
					status=200,
					mimetype='application/json',
				)
			else:
				# Third request returns success
				return Response(
					json.dumps(
						{
							'access_token': 'test-api-key',
							'token_type': 'Bearer',
							'user_id': 'test-user-123',
							'scope': 'read write',
						}
					),
					status=200,
					mimetype='application/json',
				)

		# Set up auth endpoint
		httpserver.expect_request(
			'/api/v1/oauth/device/authorize',
			method='POST',
		).respond_with_json(
			{
				'device_code': 'test-device-code',
				'user_code': 'ABCD-1234',
				'verification_uri': 'https://example.com/device',
				'verification_uri_complete': 'https://example.com/device?user_code=ABCD-1234',
				'expires_in': 1800,
				'interval': 0.1,  # Short interval for testing
			}
		)

		# Set up token endpoint with custom handler
		httpserver.expect_request(
			'/api/v1/oauth/device/token',
			method='POST',
		).respond_with_handler(handle_token_request)

		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)
		success = await auth.authenticate(agent_session_id='test-session-id', show_instructions=False)

		assert success is True
		assert auth.is_authenticated
		assert auth.api_token == 'test-api-key'
		assert auth.user_id == 'test-user-123'
		assert request_count == 3  # Verify it took 3 polls

	async def test_wait_for_authorization_timeout(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test timeout during authorization waiting."""
		# Set up auth endpoint
		httpserver.expect_request(
			'/api/v1/oauth/device/authorize',
			method='POST',
		).respond_with_json(
			{
				'device_code': 'test-device-code',
				'user_code': 'ABCD-1234',
				'verification_uri': 'https://example.com/device',
				'verification_uri_complete': 'https://example.com/device?user_code=ABCD-1234',
				'expires_in': 1800,
				'interval': 0.1,
			}
		)

		# Set up token endpoint to always return pending
		httpserver.expect_request(
			'/api/v1/oauth/device/token',
			method='POST',
		).respond_with_json(
			{
				'error': 'authorization_pending',
				'error_description': 'Authorization pending',
			}
		)

		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)

		# Call poll_for_token directly with short timeout
		result = await auth.poll_for_token('test-device-code', interval=0.1, timeout=0.5)
		assert result is None  # Should timeout and return None
		assert not auth.is_authenticated

	async def test_logout(self, temp_config_dir):
		"""Test logout functionality."""
		auth = DeviceAuthClient()

		# Save credentials directly using auth_config
		auth.auth_config.api_token = 'test-key'
		auth.auth_config.user_id = 'test-user'
		auth.auth_config.save_to_file()

		assert auth.is_authenticated is True
		assert (temp_config_dir / 'cloud_auth.json').exists()

		# Clear auth (logout)
		auth.clear_auth()

		assert auth.is_authenticated is False
		# Note: clear_auth() saves an empty config, so file still exists
		assert (temp_config_dir / 'cloud_auth.json').exists()

		# Verify the file contains empty credentials
		auth2 = DeviceAuthClient()
		assert auth2.auth_config.api_token is None
		assert auth2.auth_config.user_id is None


class TestCloudSyncService:
	"""Test CloudSyncService class."""

	@pytest.fixture
	def mock_auth(self):
		"""Mock DeviceAuthClient."""
		auth = Mock(spec=DeviceAuthClient)
		auth.is_authenticated.return_value = True
		auth.get_credentials.return_value = ('test-api-key', 'test-user-123')
		auth.start_device_flow = AsyncMock(
			return_value={
				'device_code': 'test-device-code',
				'user_code': 'ABCD-1234',
				'verification_uri_complete': 'https://example.com/device?user_code=ABCD-1234',
			}
		)
		auth.wait_for_authorization = AsyncMock(return_value=True)
		return auth

	async def test_init(self, mock_auth):
		"""Test CloudSyncService initialization."""
		service = CloudSyncService(
			agent_session_id='test-session-id',
			auth=mock_auth,
		)

		assert service.agent_session_id == 'test-session-id'
		assert service.auth == mock_auth
		assert service._authenticated is True
		assert service._pre_auth_events == []

	async def test_send_event_authenticated(self, mock_auth, mock_httpx_client):
		"""Test sending event when authenticated."""
		# Mock httpx client
		mock_client = AsyncMock()
		mock_response = MockResponse({'processed': 1, 'failed': 0})
		mock_client.post.return_value = mock_response
		mock_httpx_client.return_value.__aenter__.return_value = mock_client

		service = CloudSyncService(
			agent_session_id='test-session-id',
			auth=mock_auth,
		)

		# Send event
		event_data = {
			'task': 'Test task',
			'status': 'running',
		}

		await service.send_event(
			event_type='CreateAgentTask',
			event_data=event_data,
			event_schema='AgentTaskModel@1.0',
		)

		# Check request was made
		mock_client.post.assert_called_once()
		call_args = mock_client.post.call_args

		# Check URL
		assert call_args[0][0] == 'https://api.browser-use.com/api/v1/events/'

		# Check auth header
		assert call_args[1]['headers']['Authorization'] == 'Bearer test-api-key'

		# Check event data
		json_data = call_args[1]['json']
		assert len(json_data['events']) == 1
		event = json_data['events'][0]
		assert event['event_type'] == 'CreateAgentTask'
		assert event['data'] == event_data
		assert event['data']['user_id'] == 'test-user-123'

	async def test_send_event_pre_auth(self, mock_httpx_client):
		"""Test sending event before authentication."""
		# Mock auth that's not authenticated
		mock_auth = Mock(spec=DeviceAuthClient)
		mock_auth.is_authenticated.return_value = False

		# Mock httpx client
		mock_client = AsyncMock()
		mock_response = MockResponse({'processed': 1, 'failed': 0})
		mock_client.post.return_value = mock_response
		mock_httpx_client.return_value.__aenter__.return_value = mock_client

		service = CloudSyncService(
			agent_session_id='test-session-id',
			auth=mock_auth,
		)

		# Send event
		event_data = {
			'task': 'Test task',
			'status': 'running',
		}

		await service.send_event(
			event_type='CreateAgentTask',
			event_data=event_data,
			event_schema='AgentTaskModel@1.0',
		)

		# Check request was made without auth header
		mock_client.post.assert_called_once()
		call_args = mock_client.post.call_args
		assert 'Authorization' not in call_args[1]['headers']

		# Check event was stored for later
		assert len(service._pre_auth_events) == 1
		assert service._pre_auth_events[0]['event_type'] == 'CreateAgentTask'

	async def test_authenticate_and_resend(self, mock_auth, mock_httpx_client):
		"""Test authentication flow with pre-auth event resending."""
		# Start unauthenticated
		mock_auth.is_authenticated.side_effect = [False, False, True, True]
		mock_auth.get_credentials.return_value = ('test-api-key', 'test-user-123')

		# Mock httpx client
		mock_client = AsyncMock()
		mock_response = MockResponse({'processed': 1, 'failed': 0})
		mock_client.post.return_value = mock_response
		mock_httpx_client.return_value.__aenter__.return_value = mock_client

		service = CloudSyncService(
			agent_session_id='test-session-id',
			auth=mock_auth,
		)

		# Send pre-auth event
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Pre-auth task'},
			event_schema='AgentTaskModel@1.0',
		)

		assert len(service._pre_auth_events) == 1

		# Authenticate
		authenticated = await service.authenticate()
		assert authenticated is True

		# Pre-auth events should be cleared after resending
		assert len(service._pre_auth_events) == 0

		# Check that events were sent (1 pre-auth + 1 resend after auth)
		assert mock_client.post.call_count == 2

		# First call should be without auth
		first_call = mock_client.post.call_args_list[0]
		assert 'Authorization' not in first_call[1]['headers']

		# Second call should be with auth
		second_call = mock_client.post.call_args_list[1]
		assert second_call[1]['headers']['Authorization'] == 'Bearer test-api-key'

	async def test_error_handling(self, mock_auth, mock_httpx_client):
		"""Test error handling during event sending."""
		# Mock httpx client to raise error
		mock_client = AsyncMock()
		mock_client.post.side_effect = Exception('Network error')
		mock_httpx_client.return_value.__aenter__.return_value = mock_client

		service = CloudSyncService(
			agent_session_id='test-session-id',
			auth=mock_auth,
		)

		# Send event - should not raise
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Test task'},
			event_schema='AgentTaskModel@1.0',
		)

		# Event should still be logged even if send fails
		mock_client.post.assert_called_once()

	async def test_update_wal_events(self, mock_auth):
		"""Test updating WAL events with real user ID."""
		service = CloudSyncService(
			agent_session_id='test-session-id',
			auth=mock_auth,
		)

		# Mock WAL file
		wal_path = Path(tempfile.mktemp(suffix='.jsonl'))

		# Write test events
		events = [
			{
				'event_type': 'CreateAgentTask',
				'data': {'user_id': '99999999-9999-9999-9999-999999999999', 'task': 'Task 1'},
			},
			{
				'event_type': 'UpdateAgentTask',
				'data': {'user_id': '99999999-9999-9999-9999-999999999999', 'status': 'done'},
			},
			{
				'event_type': 'CreateAgentStep',
				'data': {'user_id': 'other-user', 'step': 1},
			},
		]

		async with aiofiles.open(wal_path, 'w') as f:
			for event in events:
				await f.write(json.dumps(event) + '\n')

		# Update WAL
		updated = await service._update_wal_events(wal_path, 'real-user-123')
		assert updated == 2  # Only temp user events should be updated

		# Read back and verify
		async with aiofiles.open(wal_path) as f:
			lines = await f.readlines()

		updated_events = [json.loads(line) for line in lines]
		assert updated_events[0]['data']['user_id'] == 'real-user-123'
		assert updated_events[1]['data']['user_id'] == 'real-user-123'
		assert updated_events[2]['data']['user_id'] == 'other-user'  # Unchanged

		# Cleanup
		wal_path.unlink()


class TestIntegration:
	"""Integration tests for OAuth2 and cloud sync."""

	@pytest.fixture
	def temp_home(self):
		"""Create temporary home directory."""
		with tempfile.TemporaryDirectory() as tmpdir:
			original_home = os.environ.get('HOME')
			os.environ['HOME'] = tmpdir
			yield Path(tmpdir)
			if original_home:
				os.environ['HOME'] = original_home
			else:
				del os.environ['HOME']

	async def test_full_auth_flow(self, temp_home, mock_httpx_client):
		"""Test complete authentication flow."""
		# Mock responses
		auth_response = MockResponse(
			{
				'device_code': 'test-device-code',
				'user_code': 'ABCD-1234',
				'verification_uri_complete': 'https://example.com/device?user_code=ABCD-1234',
				'expires_in': 1800,
				'interval': 5,
			}
		)

		token_responses = [
			MockResponse({'error': 'authorization_pending'}),
			MockResponse(
				{
					'access_token': 'test-api-key',
					'token_type': 'Bearer',
					'user_id': 'test-user-123',
				}
			),
		]

		event_response = MockResponse({'processed': 1, 'failed': 0})

		# Mock httpx client
		mock_client = AsyncMock()
		mock_client.post.side_effect = [
			event_response,  # Pre-auth event
			auth_response,  # Start device flow
			*token_responses,  # Polling
			event_response,  # Resend after auth
			event_response,  # New authenticated event
		]
		mock_httpx_client.return_value.__aenter__.return_value = mock_client

		# Create service
		service = CloudSyncService(agent_session_id='test-session-id')

		# Send pre-auth event
		await service.send_event(
			event_type='CreateAgentSession',
			event_data={'started_at': datetime.utcnow().isoformat()},
			event_schema='AgentSessionModel@1.0',
		)

		# Authenticate (with quick polling for test)
		with patch.object(service.auth, 'wait_for_authorization', side_effect=service.auth.wait_for_authorization) as mock_wait:
			mock_wait.return_value = True
			authenticated = await service.authenticate()

		assert authenticated is True
		assert service._authenticated is True

		# Send authenticated event
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Authenticated task'},
			event_schema='AgentTaskModel@1.0',
		)

		# Verify all calls were made
		assert mock_client.post.call_count == 5

		# Verify auth was saved
		config_dir = temp_home / '.config' / 'browseruse'
		auth_file = config_dir / 'cloud_auth.json'
		assert auth_file.exists()

		async with aiofiles.open(auth_file) as f:
			content = await f.read()
			saved_auth = json.loads(content)
		assert saved_auth['api_key'] == 'test-api-key'
		assert saved_auth['user_id'] == 'test-user-123'


class TestAuthResilience:
	"""Test auth resilience scenarios - agent should never break due to sync failures."""

	async def test_token_expiry_handling(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test that expired tokens are handled gracefully."""
		# Set up successful auth flow first
		httpserver.expect_request(
			'/api/v1/oauth/device/authorize',
			method='POST',
		).respond_with_json(
			{
				'device_code': 'test-device-code',
				'user_code': 'ABCD-1234',
				'verification_uri': 'https://example.com/device',
				'verification_uri_complete': 'https://example.com/device?user_code=ABCD-1234',
				'expires_in': 1800,
				'interval': 0.1,
			}
		)

		httpserver.expect_request(
			'/api/v1/oauth/device/token',
			method='POST',
		).respond_with_json(
			{
				'access_token': 'test-api-key',
				'token_type': 'Bearer',
				'user_id': 'test-user-123',
				'scope': 'read write',
			}
		)

		# Authenticate successfully first
		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)
		success = await auth.authenticate(agent_session_id='test-session-id', show_instructions=False)
		assert success is True

		# Now simulate token expiry by returning 401 errors
		httpserver.expect_request(
			'/api/v1/events/',
			method='POST',
		).respond_with_json({'error': 'unauthorized', 'detail': 'Token expired'}, status=401)

		# Create cloud sync service
		from browser_use.sync.service import CloudSyncService

		service = CloudSyncService(base_url=httpserver.url_for(''), enable_auth=True)
		service.auth_client = auth

		# Send event - should not raise exception even though token is expired
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Test task after token expiry'},
			event_schema='AgentTaskModel@1.0',
		)

		# Agent should continue functioning despite sync failure
		assert True  # No exception raised

	async def test_auth_failure_resilience(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test that auth failures don't break the agent."""
		# Set up auth endpoint to always fail
		httpserver.expect_request(
			'/api/v1/oauth/device/authorize',
			method='POST',
		).respond_with_json({'error': 'invalid_client', 'error_description': 'Client not found'}, status=400)

		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)

		# Auth should fail gracefully without throwing
		success = await auth.authenticate(agent_session_id='test-session-id', show_instructions=False)
		assert success is False

		# Should still be able to create sync service
		from browser_use.sync.service import CloudSyncService

		service = CloudSyncService(base_url=httpserver.url_for(''), enable_auth=True)
		service.auth_client = auth

		# Set up events endpoint to handle unauthenticated requests
		httpserver.expect_request(
			'/api/v1/events/',
			method='POST',
		).respond_with_json({'processed': 1, 'failed': 0})

		# Should be able to send events without auth (pre-auth mode)
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Test task without auth'},
			event_schema='AgentTaskModel@1.0',
		)

	async def test_server_downtime_resilience(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test that server downtime doesn't break the agent."""
		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)

		# Don't set up any server responses - simulate server being down

		# Auth should timeout gracefully
		result = await auth.poll_for_token('fake-device-code', interval=0.1, timeout=0.3)
		assert result is None

		from browser_use.sync.service import CloudSyncService

		service = CloudSyncService(base_url=httpserver.url_for(''), enable_auth=True)
		service.auth_client = auth

		# Should be able to send events even when server is down
		# They will be queued locally
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Test task during server downtime'},
			event_schema='AgentTaskModel@1.0',
		)

	async def test_excessive_event_queue_handling(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test that excessive event queuing doesn't break the agent."""
		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)

		from browser_use.sync.service import CloudSyncService

		service = CloudSyncService(base_url=httpserver.url_for(''), enable_auth=True)
		service.auth_client = auth

		# Send many events while server is down (no responses configured)
		for i in range(100):
			await service.send_event(
				event_type='CreateAgentTask',
				event_data={'task': f'Test task {i}'},
				event_schema='AgentTaskModel@1.0',
			)

		# Agent should still be functioning
		assert True  # No memory issues or crashes

	async def test_malformed_server_responses(self, httpserver: HTTPServer, http_client, temp_config_dir):
		"""Test that malformed server responses don't break the agent."""
		# Set up malformed JSON responses
		httpserver.expect_request(
			'/api/v1/oauth/device/authorize',
			method='POST',
		).respond_with_data('invalid json{', status=200, content_type='application/json')

		auth = DeviceAuthClient(base_url=httpserver.url_for(''), http_client=http_client)

		# Should handle malformed response gracefully
		try:
			await auth.start_device_authorization('test-session-id')
		except Exception:
			pass  # Exception is expected but shouldn't crash the agent

		# Set up another malformed response for events
		httpserver.expect_request(
			'/api/v1/events/',
			method='POST',
		).respond_with_data('malformed response', status=500)

		from browser_use.sync.service import CloudSyncService

		service = CloudSyncService(base_url=httpserver.url_for(''), enable_auth=True)
		service.auth_client = auth

		# Should handle malformed event response gracefully
		await service.send_event(
			event_type='CreateAgentTask',
			event_data={'task': 'Test task with malformed response'},
			event_schema='AgentTaskModel@1.0',
		)
