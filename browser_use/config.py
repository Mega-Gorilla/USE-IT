from __future__ import annotations

import logging
import os
import re
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any

import psutil
import yaml

logger = logging.getLogger(__name__)

ENV_PATTERN = re.compile(r'\$\{([A-Za-z0-9_]+)(?::([^}]*))?\}')

DEFAULT_CONFIG: dict[str, Any] = {
	'paths': {
		'config_home': '~/.config',
		'config_dir': '~/.config/browseruse',
		'profiles_dir': None,
		'downloads_dir': None,
		'extensions_dir': None,
		'default_user_data_dir': None,
		'cache_home': '~/.cache',
		'windows_font_dir': 'C:\\Windows\\Fonts',
	},
	'logging': {
		'level': 'info',
		'debug_log_file': None,
		'info_log_file': None,
		'cdp_level': 'WARNING',
	},
	'telemetry': {
		'anonymized': True,
		'cloud_sync': None,
		'cloud_api_url': 'https://api.browser-use.com',
		'cloud_ui_url': '',
	},
	'llm': {
		'default_model': 'gpt-4.1-mini',
		'default_temperature': None,
		'azure_endpoint': '',
		'api_keys': {
			'openai': '',
			'anthropic': '',
			'google': '',
			'groq': '',
			'novita': '',
			'deepseek': '',
			'azure': '',
		},
		'skip_api_key_verification': False,
	},
	'browser': {
		'headless': False,
		'user_data_dir': None,
		'downloads_path': None,
		'allowed_domains': [],
		'proxy': {
			'server': None,
			'bypass': [],
			'username': None,
			'password': None,
		},
	},
	'agent': {
		'language': 'en',
		'max_steps': 100,
		'max_failures': 3,
		'use_vision': 'auto',
		'max_actions_per_step': 10,
		'interactive_mode': False,
	},
}


class ConfigLoadError(RuntimeError):
	pass


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
	for key, value in override.items():
		if isinstance(value, dict) and isinstance(base.get(key), dict):
			base[key] = _deep_merge(base[key], value)
		else:
			base[key] = deepcopy(value)
	return base


def _expand_env_vars(value: Any) -> Any:
	if isinstance(value, str):
		def replacer(match: re.Match[str]) -> str:
			variable = match.group(1)
			default = match.group(2) or ''
			return os.getenv(variable, default)

		return ENV_PATTERN.sub(replacer, value)
	if isinstance(value, dict):
		return {key: _expand_env_vars(sub_value) for key, sub_value in value.items()}
	if isinstance(value, list):
		return [_expand_env_vars(item) for item in value]
	return value


def _resolve_config_file() -> Path | None:
	if env_path := os.getenv('BROWSER_USE_CONFIG_PATH'):
		path = Path(env_path).expanduser()
		if path.is_file():
			return path

	project_path = Path.cwd() / 'config.yaml'
	if project_path.is_file():
		return project_path

	user_path = Path.home() / '.config' / 'browseruse' / 'config.yaml'
	if user_path.is_file():
		return user_path

	return None


def _load_config_data() -> tuple[dict[str, Any], Path | None]:
	config = deepcopy(DEFAULT_CONFIG)
	source_path = _resolve_config_file()

	if source_path:
		try:
			with source_path.open('r', encoding='utf-8') as handle:
				loaded = yaml.safe_load(handle) or {}
				if not isinstance(loaded, dict):
					raise ConfigLoadError(
						f'Config file {source_path} must contain a mapping at the top level. '
						'Please verify the YAML structure.'
					)
				config = _deep_merge(config, loaded)
		except FileNotFoundError:
			logger.debug('Config file %s disappeared during load, falling back to defaults', source_path)
			source_path = None
		except Exception as exc:
			raise ConfigLoadError(
				f'Failed to read config file at {source_path}: {exc}\n'
				'Check the YAML syntax and file permissions.'
			) from exc

	config = _expand_env_vars(config)
	return config, source_path


def _parse_bool(value: Any, *, default: bool = False) -> bool:
	if value is None:
		return default
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
	return bool(value)


def _resolved_paths(settings: dict[str, Any]) -> dict[str, Path | str]:
	paths_settings = settings.get('paths', {})

	cache_home = Path(os.getenv('XDG_CACHE_HOME') or paths_settings.get('cache_home') or '~/.cache').expanduser()
	config_home = Path(os.getenv('XDG_CONFIG_HOME') or paths_settings.get('config_home') or '~/.config').expanduser()

	config_dir = Path(
		os.getenv('BROWSER_USE_CONFIG_DIR') or paths_settings.get('config_dir') or (config_home / 'browseruse')
	).expanduser()

	profiles_dir = Path(
		paths_settings.get('profiles_dir') or (config_dir / 'profiles')
	).expanduser()

	downloads_dir = Path(
		paths_settings.get('downloads_dir') or (config_dir / 'downloads')
	).expanduser()

	extensions_dir = Path(
		paths_settings.get('extensions_dir') or (config_dir / 'extensions')
	).expanduser()

	default_profile = Path(
		paths_settings.get('default_user_data_dir') or (profiles_dir / 'default')
	).expanduser()

	def _ensure_directory(directory: Path) -> None:
		try:
			directory.mkdir(parents=True, exist_ok=True)
			if os.name != 'nt':
				try:
					directory.chmod(0o700)
				except PermissionError:
					logger.warning(
						'Could not set permissions on %s; please ensure it is not world-readable.', directory
					)
		except Exception as exc:
			logger.warning('Failed to create directory %s: %s', directory, exc)

	for directory in (config_dir, profiles_dir, downloads_dir, extensions_dir):
		_ensure_directory(directory)

	windows_font_dir = os.getenv('WIN_FONT_DIR') or paths_settings.get('windows_font_dir') or 'C:\\Windows\\Fonts'

	return {
		'cache_home': cache_home,
		'config_home': config_home,
		'config_dir': config_dir,
		'profiles_dir': profiles_dir,
		'downloads_dir': downloads_dir,
		'extensions_dir': extensions_dir,
		'default_user_data_dir': default_profile,
		'windows_font_dir': windows_font_dir,
	}


@cache
def is_running_in_docker() -> bool:
	try:
		if Path('/.dockerenv').exists() or 'docker' in Path('/proc/1/cgroup').read_text().lower():
			return True
	except Exception:
		pass

	try:
		init_cmd = ' '.join(psutil.Process(1).cmdline())
		if ('py' in init_cmd) or ('uv' in init_cmd) or ('app' in init_cmd):
			return True
	except Exception:
		pass

	try:
		if len(psutil.pids()) < 10:
			return True
	except Exception:
		pass

	return False


class Config:
	def __init__(self) -> None:
		self._raw: dict[str, Any] | None = None
		self._source: Path | None = None

	def reload(self) -> dict[str, Any]:
		self._raw, self._source = _load_config_data()
		return deepcopy(self._raw)

	def _settings(self) -> dict[str, Any]:
		if self._raw is None:
			self.reload()
		return self._raw or {}

	@property
	def data(self) -> dict[str, Any]:
		return deepcopy(self._settings())

	@property
	def source(self) -> Path | None:
		self._settings()
		return self._source

	def load_config(self, *, reload: bool = False) -> dict[str, Any]:
		if reload or self._raw is None:
			self.reload()

		settings = self._settings()
		paths = _resolved_paths(settings)

		browser_settings = deepcopy(settings.get('browser', {}))
		llm_settings = deepcopy(settings.get('llm', {}))
		agent_settings = deepcopy(settings.get('agent', {}))

		browser_profile = {
			'headless': _parse_bool(browser_settings.get('headless'), default=False),
			'user_data_dir': str(browser_settings.get('user_data_dir') or paths['default_user_data_dir']),
			'downloads_path': str(browser_settings.get('downloads_path') or paths['downloads_dir']),
			'allowed_domains': list(browser_settings.get('allowed_domains') or []),
		}

		proxy_settings = browser_settings.get('proxy') or {}
		bypass_value = proxy_settings.get('bypass')
		if isinstance(bypass_value, (list, tuple, set)):
			bypass_entries = [str(entry).strip() for entry in bypass_value if str(entry).strip()]
			proxy_settings = {**proxy_settings, 'bypass': ','.join(bypass_entries) if bypass_entries else None}

		if any(proxy_settings.get(key) for key in ['server', 'bypass', 'username', 'password']):
			proxy_payload: dict[str, Any] = {}
			if proxy_settings.get('server'):
				proxy_payload['server'] = proxy_settings['server']
			if proxy_settings.get('bypass'):
				proxy_payload['bypass'] = proxy_settings['bypass']
			if proxy_settings.get('username'):
				proxy_payload['username'] = proxy_settings['username']
			if proxy_settings.get('password'):
				proxy_payload['password'] = proxy_settings['password']
			if proxy_payload:
				browser_profile['proxy'] = proxy_payload

		llm_payload = {
			'model': llm_settings.get('default_model', ''),
			'temperature': llm_settings.get('default_temperature'),
			'api_key': (llm_settings.get('api_keys') or {}).get('openai', ''),
		}

		agent_payload = {
			'language': agent_settings.get('language', 'en'),
			'max_steps': agent_settings.get('max_steps', 100),
			'max_failures': agent_settings.get('max_failures', 3),
			'use_vision': agent_settings.get('use_vision', 'auto'),
			'max_actions_per_step': agent_settings.get('max_actions_per_step', 10),
			'interactive_mode': _parse_bool(agent_settings.get('interactive_mode'), default=False),
		}

		config = {
			'browser_profile': browser_profile,
			'llm': llm_payload,
			'agent': agent_payload,
		}

		return _apply_env_overrides(config)

	@property
	def BROWSER_USE_LOGGING_LEVEL(self) -> str:
		settings = self._settings()
		base_level = str(settings.get('logging', {}).get('level', 'info')).lower()
		return os.getenv('BROWSER_USE_LOGGING_LEVEL', base_level).lower()

	@property
	def CDP_LOGGING_LEVEL(self) -> str:
		settings = self._settings()
		default_level = str(settings.get('logging', {}).get('cdp_level', 'WARNING')).upper()
		return os.getenv('CDP_LOGGING_LEVEL', default_level).upper()

	@property
	def BROWSER_USE_DEBUG_LOG_FILE(self) -> str | None:
		settings = self._settings()
		file_value = settings.get('logging', {}).get('debug_log_file')
		override = os.getenv('BROWSER_USE_DEBUG_LOG_FILE')
		return override or (str(file_value) if file_value else None)

	@property
	def BROWSER_USE_INFO_LOG_FILE(self) -> str | None:
		settings = self._settings()
		file_value = settings.get('logging', {}).get('info_log_file')
		override = os.getenv('BROWSER_USE_INFO_LOG_FILE')
		return override or (str(file_value) if file_value else None)

	@property
	def ANONYMIZED_TELEMETRY(self) -> bool:
		settings = self._settings()
		config_value = settings.get('telemetry', {}).get('anonymized', True)
		env_value = os.getenv('ANONYMIZED_TELEMETRY')
		if env_value is not None:
			return _parse_bool(env_value, default=True)
		return _parse_bool(config_value, default=True)

	@property
	def BROWSER_USE_CLOUD_SYNC(self) -> bool:
		env_value = os.getenv('BROWSER_USE_CLOUD_SYNC')
		if env_value is not None:
			return _parse_bool(env_value, default=False)

		settings = self._settings()
		config_value = settings.get('telemetry', {}).get('cloud_sync')
		if config_value is None:
			return self.ANONYMIZED_TELEMETRY
		return _parse_bool(config_value, default=self.ANONYMIZED_TELEMETRY)

	@property
	def BROWSER_USE_CLOUD_API_URL(self) -> str:
		settings = self._settings()
		default_url = settings.get('telemetry', {}).get('cloud_api_url', 'https://api.browser-use.com')
		return os.getenv('BROWSER_USE_CLOUD_API_URL', default_url)

	@property
	def BROWSER_USE_CLOUD_UI_URL(self) -> str:
		settings = self._settings()
		default_url = settings.get('telemetry', {}).get('cloud_ui_url', '')
		return os.getenv('BROWSER_USE_CLOUD_UI_URL', default_url)

	@property
	def DEFAULT_LLM(self) -> str:
		settings = self._settings()
		default_model = settings.get('llm', {}).get('default_model', '')
		return os.getenv('DEFAULT_LLM', default_model)

	@property
	def SKIP_LLM_API_KEY_VERIFICATION(self) -> bool:
		env_value = os.getenv('SKIP_LLM_API_KEY_VERIFICATION')
		if env_value is not None:
			return _parse_bool(env_value, default=False)
		settings = self._settings()
		return _parse_bool(settings.get('llm', {}).get('skip_api_key_verification'), default=False)

	@property
	def OPENAI_API_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('openai', '')
		return os.getenv('OPENAI_API_KEY', fallback)

	@property
	def ANTHROPIC_API_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('anthropic', '')
		return os.getenv('ANTHROPIC_API_KEY', fallback)

	@property
	def GOOGLE_API_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('google', '')
		return os.getenv('GOOGLE_API_KEY', fallback)

	@property
	def DEEPSEEK_API_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('deepseek', '')
		return os.getenv('DEEPSEEK_API_KEY', fallback)

	@property
	def GROK_API_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('grok', '')
		return os.getenv('GROK_API_KEY', fallback)

	@property
	def NOVITA_API_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('novita', '')
		return os.getenv('NOVITA_API_KEY', fallback)

	@property
	def AZURE_OPENAI_ENDPOINT(self) -> str:
		settings = self._settings()
		fallback = settings.get('llm', {}).get('azure_endpoint', '')
		return os.getenv('AZURE_OPENAI_ENDPOINT', fallback)

	@property
	def AZURE_OPENAI_KEY(self) -> str:
		settings = self._settings()
		fallback = (settings.get('llm', {}).get('api_keys') or {}).get('azure', '')
		return os.getenv('AZURE_OPENAI_KEY', fallback)

	@property
	def IN_DOCKER(self) -> bool:
		env_value = os.getenv('IN_DOCKER')
		if env_value is not None:
			return _parse_bool(env_value, default=False)
		return is_running_in_docker()

	@property
	def IS_IN_EVALS(self) -> bool:
		env_value = os.getenv('IS_IN_EVALS')
		return _parse_bool(env_value, default=False)

	@property
	def XDG_CACHE_HOME(self) -> Path:
		return _resolved_paths(self._settings())['cache_home']

	@property
	def XDG_CONFIG_HOME(self) -> Path:
		return _resolved_paths(self._settings())['config_home']

	@property
	def BROWSER_USE_CONFIG_DIR(self) -> Path:
		return _resolved_paths(self._settings())['config_dir']

	@property
	def BROWSER_USE_CONFIG_FILE(self) -> Path:
		return self.BROWSER_USE_CONFIG_DIR / 'config.yaml'

	@property
	def BROWSER_USE_PROFILES_DIR(self) -> Path:
		return _resolved_paths(self._settings())['profiles_dir']

	@property
	def BROWSER_USE_DEFAULT_USER_DATA_DIR(self) -> Path:
		return _resolved_paths(self._settings())['default_user_data_dir']

	@property
	def BROWSER_USE_DOWNLOADS_DIR(self) -> Path:
		return _resolved_paths(self._settings())['downloads_dir']

	@property
	def BROWSER_USE_EXTENSIONS_DIR(self) -> Path:
		return _resolved_paths(self._settings())['extensions_dir']

	@property
	def WIN_FONT_DIR(self) -> str:
		return str(_resolved_paths(self._settings())['windows_font_dir'])


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
	result = deepcopy(config)

	if (headless := os.getenv('BROWSER_USE_HEADLESS')) is not None:
		result['browser_profile']['headless'] = _parse_bool(headless, default=result['browser_profile'].get('headless', False))

	if (allowed_domains := os.getenv('BROWSER_USE_ALLOWED_DOMAINS')):
		result['browser_profile']['allowed_domains'] = [
			domain.strip() for domain in allowed_domains.split(',') if domain.strip()
		]

	proxy_env = {
		'server': os.getenv('BROWSER_USE_PROXY_URL'),
		'bypass': os.getenv('BROWSER_USE_NO_PROXY'),
		'username': os.getenv('BROWSER_USE_PROXY_USERNAME'),
		'password': os.getenv('BROWSER_USE_PROXY_PASSWORD'),
	}

	existing_proxy: dict[str, Any] = dict(result['browser_profile'].get('proxy') or {})
	if proxy_env['server']:
		existing_proxy['server'] = proxy_env['server']
	if proxy_env['bypass']:
		bypass_entries = [entry.strip() for entry in proxy_env['bypass'].split(',') if entry.strip()]
		if bypass_entries:
			existing_proxy['bypass'] = ','.join(bypass_entries)
	if proxy_env['username']:
		existing_proxy['username'] = proxy_env['username']
	if proxy_env['password']:
		existing_proxy['password'] = proxy_env['password']

	if existing_proxy:
		result['browser_profile']['proxy'] = existing_proxy

	llm_section = result.setdefault('llm', {})
	if os.getenv('OPENAI_API_KEY'):
		llm_section['api_key'] = os.getenv('OPENAI_API_KEY', '')

	if os.getenv('BROWSER_USE_LLM_MODEL'):
		llm_section['model'] = os.getenv('BROWSER_USE_LLM_MODEL', '')

	return result


CONFIG = Config()


def load_config_yaml(*, reload: bool = False) -> dict[str, Any]:
	if reload:
		return CONFIG.reload()
	return CONFIG.data


def load_browser_use_config(*, reload: bool = False) -> dict[str, Any]:
	return CONFIG.load_config(reload=reload)


def get_default_profile(config: dict[str, Any]) -> dict[str, Any]:
	return deepcopy(config.get('browser_profile', {}))


def get_default_llm(config: dict[str, Any]) -> dict[str, Any]:
	return deepcopy(config.get('llm', {}))


def get_default_agent(config: dict[str, Any]) -> dict[str, Any]:
	return deepcopy(config.get('agent', {}))
