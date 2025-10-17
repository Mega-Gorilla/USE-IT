"""Tests for the YAML-backed configuration system."""

import os
from pathlib import Path

import pytest

from browser_use.config import CONFIG, load_browser_use_config, load_config_yaml


class TestLazyConfig:
	"""Test lazy loading of environment variables through CONFIG object."""

	def test_config_reads_env_vars_lazily(self):
		"""Test that CONFIG reads environment variables each time they're accessed."""
		# Set an env var
		original_value = os.environ.get('BROWSER_USE_LOGGING_LEVEL', '')
		try:
			os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'debug'
			assert CONFIG.BROWSER_USE_LOGGING_LEVEL == 'debug'

			# Change the env var
			os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'info'
			assert CONFIG.BROWSER_USE_LOGGING_LEVEL == 'info'

			# Delete the env var to test default
			del os.environ['BROWSER_USE_LOGGING_LEVEL']
			assert CONFIG.BROWSER_USE_LOGGING_LEVEL == 'info'  # default value
		finally:
			# Restore original value
			if original_value:
				os.environ['BROWSER_USE_LOGGING_LEVEL'] = original_value
			else:
				os.environ.pop('BROWSER_USE_LOGGING_LEVEL', None)

	def test_boolean_env_vars(self):
		"""Test boolean environment variables are parsed correctly."""
		original_value = os.environ.get('ANONYMIZED_TELEMETRY', '')
		try:
			# Test true values
			for true_val in ['true', 'True', 'TRUE', 'yes', 'Yes', '1']:
				os.environ['ANONYMIZED_TELEMETRY'] = true_val
				assert CONFIG.ANONYMIZED_TELEMETRY is True, f'Failed for value: {true_val}'

			# Test false values
			for false_val in ['false', 'False', 'FALSE', 'no', 'No', '0']:
				os.environ['ANONYMIZED_TELEMETRY'] = false_val
				assert CONFIG.ANONYMIZED_TELEMETRY is False, f'Failed for value: {false_val}'
		finally:
			if original_value:
				os.environ['ANONYMIZED_TELEMETRY'] = original_value
			else:
				os.environ.pop('ANONYMIZED_TELEMETRY', None)

	def test_api_keys_lazy_loading(self):
		"""Test API keys are loaded lazily."""
		original_value = os.environ.get('OPENAI_API_KEY', '')
		try:
			# Test empty default
			os.environ.pop('OPENAI_API_KEY', None)
			assert CONFIG.OPENAI_API_KEY == ''

			# Set a value
			os.environ['OPENAI_API_KEY'] = 'test-key-123'
			assert CONFIG.OPENAI_API_KEY == 'test-key-123'

			# Change the value
			os.environ['OPENAI_API_KEY'] = 'new-key-456'
			assert CONFIG.OPENAI_API_KEY == 'new-key-456'
		finally:
			if original_value:
				os.environ['OPENAI_API_KEY'] = original_value
			else:
				os.environ.pop('OPENAI_API_KEY', None)

	def test_path_configuration(self):
		"""Test path configuration variables."""
		original_value = os.environ.get('XDG_CACHE_HOME', '')
		try:
			# Test custom path
			test_path = '/tmp/test-cache'
			os.environ['XDG_CACHE_HOME'] = test_path
			# Use Path().resolve() to handle symlinks (e.g., /tmp -> /private/tmp on macOS)
			from pathlib import Path

			assert CONFIG.XDG_CACHE_HOME == Path(test_path).resolve()

			# Test default path expansion
			os.environ.pop('XDG_CACHE_HOME', None)
			assert '/.cache' in str(CONFIG.XDG_CACHE_HOME)
		finally:
			if original_value:
				os.environ['XDG_CACHE_HOME'] = original_value
			else:
				os.environ.pop('XDG_CACHE_HOME', None)

	def test_cloud_sync_inherits_telemetry(self):
		"""Test BROWSER_USE_CLOUD_SYNC inherits from ANONYMIZED_TELEMETRY when not set."""
		telemetry_original = os.environ.get('ANONYMIZED_TELEMETRY', '')
		sync_original = os.environ.get('BROWSER_USE_CLOUD_SYNC', '')
		try:
			# When BROWSER_USE_CLOUD_SYNC is not set, it should inherit from ANONYMIZED_TELEMETRY
			os.environ['ANONYMIZED_TELEMETRY'] = 'true'
			os.environ.pop('BROWSER_USE_CLOUD_SYNC', None)
			assert CONFIG.BROWSER_USE_CLOUD_SYNC is True

			os.environ['ANONYMIZED_TELEMETRY'] = 'false'
			os.environ.pop('BROWSER_USE_CLOUD_SYNC', None)
			assert CONFIG.BROWSER_USE_CLOUD_SYNC is False

			# When explicitly set, it should use its own value
			os.environ['ANONYMIZED_TELEMETRY'] = 'false'
			os.environ['BROWSER_USE_CLOUD_SYNC'] = 'true'
			assert CONFIG.BROWSER_USE_CLOUD_SYNC is True
		finally:
			if telemetry_original:
				os.environ['ANONYMIZED_TELEMETRY'] = telemetry_original
			else:
				os.environ.pop('ANONYMIZED_TELEMETRY', None)
			if sync_original:
				os.environ['BROWSER_USE_CLOUD_SYNC'] = sync_original
			else:
				os.environ.pop('BROWSER_USE_CLOUD_SYNC', None)

	def test_load_config_yaml_expands_env_vars(self, tmp_path):
		"""Ensure ${VAR} placeholders inside YAML are expanded using environment variables."""
		config_path = tmp_path / 'test-config.yaml'
		config_path.write_text('llm:\n  api_keys:\n    openai: ${TEST_OPENAI_KEY}\n', encoding='utf-8')

		original_config_path = os.environ.get('BROWSER_USE_CONFIG_PATH')
		try:
			os.environ['TEST_OPENAI_KEY'] = 'secret-123'
			os.environ['BROWSER_USE_CONFIG_PATH'] = str(config_path)

			config = load_config_yaml(reload=True)
			assert config['llm']['api_keys']['openai'] == 'secret-123'
		finally:
			os.environ.pop('TEST_OPENAI_KEY', None)
			if original_config_path is None:
				os.environ.pop('BROWSER_USE_CONFIG_PATH', None)
			else:
				os.environ['BROWSER_USE_CONFIG_PATH'] = original_config_path
			CONFIG.reload()

	def test_proxy_env_vars_merge(self, tmp_path):
		"""Proxy environment variables should merge with existing config values."""
		config_path = tmp_path / 'config-with-proxy.yaml'
		config_path.write_text(
			'browser:\n  proxy:\n    server: http://config.example.com:8080\n    username: config-user\n',
			encoding='utf-8',
		)

		original_config_path = os.environ.get('BROWSER_USE_CONFIG_PATH')
		original_proxy_env = {
			'BROWSER_USE_PROXY_URL': os.environ.get('BROWSER_USE_PROXY_URL'),
			'BROWSER_USE_PROXY_USERNAME': os.environ.get('BROWSER_USE_PROXY_USERNAME'),
			'BROWSER_USE_PROXY_PASSWORD': os.environ.get('BROWSER_USE_PROXY_PASSWORD'),
		}
		try:
			os.environ['BROWSER_USE_CONFIG_PATH'] = str(config_path)
			CONFIG.reload()

			initial_config = load_browser_use_config()
			assert initial_config['browser_profile']['proxy']['server'] == 'http://config.example.com:8080'
			assert initial_config['browser_profile']['proxy']['username'] == 'config-user'
			assert 'password' not in initial_config['browser_profile']['proxy']

			os.environ['BROWSER_USE_PROXY_PASSWORD'] = 'env-secret'
			updated_config = load_browser_use_config(reload=True)
			assert updated_config['browser_profile']['proxy']['server'] == 'http://config.example.com:8080'
			assert updated_config['browser_profile']['proxy']['username'] == 'config-user'
			assert updated_config['browser_profile']['proxy']['password'] == 'env-secret'
		finally:
			if original_config_path is None:
				os.environ.pop('BROWSER_USE_CONFIG_PATH', None)
			else:
				os.environ['BROWSER_USE_CONFIG_PATH'] = original_config_path
			for key, value in original_proxy_env.items():
				if value is None:
					os.environ.pop(key, None)
				else:
					os.environ[key] = value
			CONFIG.reload()

	def test_env_file_is_not_auto_loaded(self, tmp_path, monkeypatch):
		"""Ensure that .env files are ignored unless explicitly loaded by the user."""
		workspace = tmp_path / 'workspace'
		workspace.mkdir()
		(workspace / '.env').write_text('OPENAI_API_KEY=from_env_file\n', encoding='utf-8')

		monkeypatch.chdir(workspace)
		monkeypatch.delenv('OPENAI_API_KEY', raising=False)
		CONFIG.reload()

		assert CONFIG.OPENAI_API_KEY == '', 'OPENAI_API_KEY should not be pulled from .env automatically'

	def test_project_level_config_yaml_is_used(self, tmp_path, monkeypatch):
		"""Project-local config.yaml should be preferred over user config."""
		user_home = tmp_path / 'home'
		user_home.mkdir()
		user_config = user_home / '.config' / 'browseruse'
		user_config.mkdir(parents=True, exist_ok=True)
		(user_config / 'config.yaml').write_text('logging:\n  level: warning\n', encoding='utf-8')

		project = tmp_path / 'project'
		project.mkdir()
		(project / 'config.yaml').write_text('logging:\n  level: debug\n', encoding='utf-8')

		monkeypatch.setenv('HOME', str(user_home))
		monkeypatch.setenv('USERPROFILE', str(user_home))
		monkeypatch.chdir(project)
		CONFIG.reload()

		assert CONFIG.BROWSER_USE_LOGGING_LEVEL == 'debug'

	@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission check only')
	def test_config_directories_have_secure_permissions(self, tmp_path, monkeypatch):
		"""Test that config directories are created with 0700 permissions on POSIX systems."""
		home = tmp_path / 'home'
		monkeypatch.setenv('HOME', str(home))
		monkeypatch.setenv('USERPROFILE', str(home))
		monkeypatch.delenv('BROWSER_USE_CONFIG_DIR', raising=False)
		CONFIG.reload()
		config_dir = CONFIG.BROWSER_USE_CONFIG_DIR
		assert (config_dir.stat().st_mode & 0o777) == 0o700

	def test_config_path_resolution_priority(self, tmp_path, monkeypatch):
		"""Ensure config resolution prefers explicit path, then cwd, then user config."""
		workspace = tmp_path / 'workspace'
		workspace.mkdir()
		monkeypatch.chdir(workspace)

		home_dir = tmp_path / 'home'
		home_dir.mkdir()
		monkeypatch.setenv('HOME', str(home_dir))
		monkeypatch.setenv('USERPROFILE', str(home_dir))

		# Prepare user config (lowest priority)
		user_config = home_dir / '.config' / 'browseruse' / 'config.yaml'
		user_config.parent.mkdir(parents=True, exist_ok=True)
		user_config.write_text('logging:\n  level: warning\n', encoding='utf-8')

		# Prepare project config (middle priority)
		project_config = workspace / 'config.yaml'
		project_config.write_text('logging:\n  level: info\n', encoding='utf-8')

		# Prepare explicit config (highest priority)
		explicit_config = tmp_path / 'explicit-config.yaml'
		explicit_config.write_text('logging:\n  level: debug\n', encoding='utf-8')

		original_config_path = os.environ.get('BROWSER_USE_CONFIG_PATH')
		try:
			# Highest priority: explicit env path
			os.environ['BROWSER_USE_CONFIG_PATH'] = str(explicit_config)
			CONFIG.reload()
			config = load_config_yaml()
			assert config['logging']['level'] == 'debug'

			# Remove explicit override -> should use project config
			os.environ.pop('BROWSER_USE_CONFIG_PATH', None)
			CONFIG.reload()
			config = load_config_yaml(reload=True)
			assert config['logging']['level'] == 'info'

			# Remove project config -> should fall back to user config
			project_config.unlink()
			CONFIG.reload()
			config = load_config_yaml(reload=True)
			assert config['logging']['level'] == 'warning'
		finally:
			if original_config_path is not None:
				os.environ['BROWSER_USE_CONFIG_PATH'] = original_config_path
			else:
				os.environ.pop('BROWSER_USE_CONFIG_PATH', None)
			CONFIG.reload()
