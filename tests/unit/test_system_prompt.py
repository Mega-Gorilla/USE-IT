import pytest

from browser_use.agent.prompt import DEFAULT_PROMPT_LANGUAGE, SystemPrompt, normalize_prompt_language


def test_system_prompt_loads_english_template():
	prompt = SystemPrompt(language='en')
	content = prompt.get_system_message().content

	assert 'You are an AI agent designed to operate in an iterative loop' in content
	assert '{max_actions}' not in content  # format 済み


def test_system_prompt_loads_japanese_template():
	prompt = SystemPrompt(language='jp')
	content = prompt.get_system_message().content

	assert 'あなたは、ブラウザタスクを自動化するために反復ループで動作する' in content


def test_system_prompt_falls_back_to_default_language(caplog):
	with caplog.at_level('WARNING'):
		prompt = SystemPrompt(language='fr')

	content = prompt.get_system_message().content

	assert 'You are an AI agent designed to operate in an iterative loop' in content
	assert any('Unsupported system prompt language' in record.message for record in caplog.records)


@pytest.mark.parametrize(
	'raw, expected',
	[
		(None, DEFAULT_PROMPT_LANGUAGE),
		('EN', 'en'),
		('jp', 'jp'),
		('Fr', DEFAULT_PROMPT_LANGUAGE),
	],
)
def test_normalize_prompt_language(raw, expected):
	assert normalize_prompt_language(raw) == expected
