import pytest

from browser_use.agent.config import AgentConfig
from browser_use.agent.prompt import DEFAULT_PROMPT_LANGUAGE, SystemPrompt, normalize_prompt_language


def test_agent_config_language_passthrough():
	config = AgentConfig(task='test', language='jp')
	assert config.language == 'jp'


def test_agent_config_language_fallback():
	config = AgentConfig(task='test', language='invalid')
	assert config.language == DEFAULT_PROMPT_LANGUAGE


@pytest.mark.parametrize(
	'input_value, expected',
	[
		(None, DEFAULT_PROMPT_LANGUAGE),
		('EN', 'en'),
		('  jp  ', 'jp'),
		('', DEFAULT_PROMPT_LANGUAGE),
		('fr', DEFAULT_PROMPT_LANGUAGE),
	],
)
def test_normalize_prompt_language_general(input_value, expected):
	assert normalize_prompt_language(input_value) == expected


def test_system_prompt_uses_language_directory(tmp_path):
	system_prompt = SystemPrompt(language='jp')
	content = system_prompt.get_system_message().content
	assert 'ブラウザタスクを自動化するために反復ループ' in content
