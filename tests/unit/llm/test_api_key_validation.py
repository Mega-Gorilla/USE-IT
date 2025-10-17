import pytest

from browser_use.config import CONFIG
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.azure.chat import ChatAzureOpenAI
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.groq.chat import ChatGroq
from browser_use.llm.openai.chat import ChatOpenAI


@pytest.fixture(autouse=True)
def clear_llm_env(monkeypatch):
	"""Ensure LLM-related environment variables are cleared before each test."""
	keys = [
		'OPENAI_API_KEY',
		'ANTHROPIC_API_KEY',
		'AZURE_OPENAI_KEY',
		'AZURE_OPENAI_API_KEY',
		'AZURE_OPENAI_ENDPOINT',
		'GOOGLE_API_KEY',
		'GROK_API_KEY',
	]
	for key in keys:
		monkeypatch.delenv(key, raising=False)

	CONFIG.reload()


def test_openai_requires_api_key():
	with pytest.raises(ModelProviderError) as exc:
		ChatOpenAI(model='gpt-4o-mini')
	assert 'OpenAI API key is missing' in str(exc.value)


def test_google_requires_api_key_or_credentials():
	with pytest.raises(ModelProviderError) as exc:
		ChatGoogle(model='gemini-2.0-flash')
	assert 'Google API key is missing' in str(exc.value)


def test_anthropic_requires_api_key():
	with pytest.raises(ModelProviderError) as exc:
		ChatAnthropic(model='claude-3-5-haiku-latest')
	assert 'Anthropic API key is missing' in str(exc.value)


def test_azure_requires_api_key(monkeypatch):
	# Provide endpoint so the error focuses on the missing key.
	monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://example.openai.azure.com')
	CONFIG.reload()
	with pytest.raises(ModelProviderError) as exc:
		ChatAzureOpenAI(model='gpt-4o')
	assert 'API key' in str(exc.value)


def test_azure_requires_endpoint(monkeypatch):
	monkeypatch.setenv('AZURE_OPENAI_KEY', 'fake-key')
	CONFIG.reload()
	with pytest.raises(ModelProviderError) as exc:
		ChatAzureOpenAI(model='gpt-4o')
	assert 'endpoint' in str(exc.value)


def test_groq_requires_api_key():
	with pytest.raises(ModelProviderError) as exc:
		ChatGroq(model='meta-llama/llama-4-maverick-17b-128e-instruct')
	assert 'Groq API key is missing' in str(exc.value)
