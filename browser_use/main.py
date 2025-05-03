import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import langchain_anthropic
import langchain_google_genai
import langchain_openai
from dotenv import load_dotenv

try:
	import readline

	READLINE_AVAILABLE = True
except ImportError:
	# readline not available on Windows by default
	READLINE_AVAILABLE = False

from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig, Controller
from browser_use.agent.views import AgentSettings

# ASCII art logo with emoji
LOGO = """\033[34m
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│                                  \033[97m   ++++++   +++++++++   \033[0m\033[34m                                │
│                                  \033[97m +++     +++++     +++  \033[0m\033[34m                                │
│                                  \033[97m ++    ++++   ++    ++  \033[0m\033[34m                                │
│                                  \033[97m ++  +++       +++  ++  \033[0m\033[34m                                │
│                                  \033[97m   ++++          +++    \033[0m\033[34m                                │
│                                  \033[97m  +++             +++   \033[0m\033[34m                                │
│                                  \033[97m +++               +++  \033[0m\033[34m                                │
│                                  \033[97m ++   +++      +++  ++  \033[0m\033[34m                                │
│                                  \033[97m ++    ++++   ++    ++  \033[0m\033[34m                                │
│                                  \033[97m +++     ++++++    +++  \033[0m\033[34m                                │
│                                  \033[97m   ++++++    +++++++    \033[0m\033[34m                                │
│                                                                                          │
│ \033[97m██████╗ ██████╗  ██████╗ ██╗    ██╗███████╗███████╗██████╗\033[0m\033[34m     \033[38;5;208m██╗   ██╗███████╗███████╗\033[0m\033[34m │
│ \033[97m██╔══██╗██╔══██╗██╔═══██╗██║    ██║██╔════╝██╔════╝██╔══██╗\033[0m\033[34m    \033[38;5;208m██║   ██║██╔════╝██╔════╝\033[0m\033[34m │
│ \033[97m██████╔╝██████╔╝██║   ██║██║ █╗ ██║███████╗█████╗  ██████╔╝\033[0m\033[34m    \033[38;5;208m██║   ██║███████╗█████╗\033[0m\033[34m   │
│ \033[97m██╔══██╗██╔══██╗██║   ██║██║███╗██║╚════██║██╔══╝  ██╔══██╗\033[0m\033[34m    \033[38;5;208m██║   ██║╚════██║██╔══╝\033[0m\033[34m   │
│ \033[97m██████╔╝██║  ██║╚██████╔╝╚███╔███╔╝███████║███████╗██║  ██║\033[0m\033[34m    \033[38;5;208m╚██████╔╝███████║███████╗\033[0m\033[34m │
│ \033[97m╚═════╝ ╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝ ╚══════╝╚══════╝╚═╝  ╚═╝\033[0m\033[34m     \033[38;5;208m╚═════╝ ╚══════╝╚══════╝\033[0m\033[34m │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│ Welcome to the browser-use \033[38;5;208mCLI\033[34m!  Run at scale on our cloud: \033[97m\033]8;;https://browser-use.com\033\\https://browser-use.com\033]8;;\033\\ \033[0m\033[34m \033[5;97m☁️\033[0m\033[34m  │
│                                                                                          │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Chat & share on Discord: \033[38;5;93m🚀 \033]8;;https://discord.gg/ESAUZAdxXY\033\\https://discord.gg/ESAUZAdxXY\033]8;;\033\\ \033[34m                               │ 
│ Get prompt inspiration:  \033[35m🦸 \033]8;;https://github.com/browser-use/awesome-prompts\033\\https://github.com/browser-use/awesome-prompts\033]8;;\033\\ \033[34m              │ 
│                          \033[35m📚 \033]8;;https://github.com/browser-use/browser-use/tree/main/examples\033\\https://github.com/browser-use/browser-use/tree/main/examples\033]8;;\033\\\033[34m│ 
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ \033[38;5;240mReport any issues:      \033[32m 🐛 \033]8;;https://github.com/browser-use/browser-use/issues\033\\https://github.com/browser-use/browser-use/issues\033]8;;\033\\ \033[34m           │
└──────────────────────────────────────────────────────────────────────────────────────────┘
\033[0m"""

# User settings file
USER_CONFIG_FILE = Path.home() / '.browser_use.json'
MAX_HISTORY_LENGTH = 100


def load_user_config() -> dict[str, Any]:
	"""Load user configuration from file."""
	if not USER_CONFIG_FILE.exists():
		# Create default config
		config = {
			'model': {
				'name': None,
				'temperature': 0.0,
				'api_keys': {
					'openai': os.getenv('OPENAI_API_KEY', ''),
					'anthropic': os.getenv('ANTHROPIC_API_KEY', ''),
					'google': os.getenv('GOOGLE_API_KEY', ''),
				},
			},
			'agent': {},  # AgentSettings will use defaults
			'browser': {
				'headless': True,
				'window_width': 1280,
				'window_height': 720,
			},
			'browser_context': {
				'keep_alive': True,
				'ignore_https_errors': False,
				'viewport_width': 1280,
				'viewport_height': 720,
			},
			'command_history': [],
		}
		save_user_config(config)
		return config

	try:
		with open(USER_CONFIG_FILE) as f:
			data = json.load(f)
			# Ensure data is a dictionary, not a list
			if isinstance(data, list):
				# If it's a list, it's probably just command history from previous version
				return {
					'model': {
						'name': None,
						'temperature': 0.0,
						'api_keys': {
							'openai': os.getenv('OPENAI_API_KEY', ''),
							'anthropic': os.getenv('ANTHROPIC_API_KEY', ''),
							'google': os.getenv('GOOGLE_API_KEY', ''),
						},
					},
					'agent': {},
					'browser': {
						'headless': True,
						'window_width': 1280,
						'window_height': 720,
					},
					'browser_context': {
						'keep_alive': True,
						'ignore_https_errors': False,
						'viewport_width': 1280,
						'viewport_height': 720,
					},
					'command_history': data,  # Use the list as command history
				}
			return data
	except (json.JSONDecodeError, FileNotFoundError):
		# If file is corrupted, start with empty config
		return {
			'model': {
				'name': None,
				'temperature': 0.0,
				'api_keys': {
					'openai': os.getenv('OPENAI_API_KEY', ''),
					'anthropic': os.getenv('ANTHROPIC_API_KEY', ''),
					'google': os.getenv('GOOGLE_API_KEY', ''),
				},
			},
			'agent': {},
			'browser': {
				'headless': True,
				'window_width': 1280,
				'window_height': 720,
			},
			'browser_context': {
				'keep_alive': True,
				'ignore_https_errors': False,
				'viewport_width': 1280,
				'viewport_height': 720,
			},
			'command_history': [],
		}


def save_user_config(config: dict[str, Any]) -> None:
	"""Save user configuration to file."""
	# Ensure command history doesn't exceed maximum length
	if 'command_history' in config and isinstance(config['command_history'], list):
		if len(config['command_history']) > MAX_HISTORY_LENGTH:
			config['command_history'] = config['command_history'][-MAX_HISTORY_LENGTH:]

	# Create parent directories if they don't exist
	USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

	with open(USER_CONFIG_FILE, 'w') as f:
		json.dump(config, f, indent=2)


def update_config_with_args(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
	"""Update configuration with command-line arguments."""
	# Ensure required sections exist
	if 'model' not in config:
		config['model'] = {}
	if 'browser' not in config:
		config['browser'] = {}
	if 'browser_context' not in config:
		config['browser_context'] = {}

	# Update configuration with command-line args if provided
	if args.model:
		config['model']['name'] = args.model
	if args.headless is not None:
		config['browser']['headless'] = args.headless
	if args.window_width:
		config['browser']['window_width'] = args.window_width
		if 'viewport_width' in config['browser_context']:
			config['browser_context']['viewport_width'] = args.window_width
	if args.window_height:
		config['browser']['window_height'] = args.window_height
		if 'viewport_height' in config['browser_context']:
			config['browser_context']['viewport_height'] = args.window_height

	return config


def setup_readline_history(history: list[str]) -> None:
	"""Set up readline with command history."""
	if not READLINE_AVAILABLE:
		return

	# Add history items to readline
	for item in history:
		readline.add_history(item)


def get_user_input(prompt: str, config: dict[str, Any]) -> str:
	"""Get user input with history support."""
	history = config.get('command_history', [])

	if READLINE_AVAILABLE:
		setup_readline_history(history)
		try:
			user_input = input(prompt)
			if user_input.strip() and (not history or user_input != history[-1]):
				history.append(user_input)
				config['command_history'] = history
				save_user_config(config)
			return user_input
		except (KeyboardInterrupt, EOFError):
			print('\n')
			raise
	else:
		# Fallback for systems without readline
		user_input = input(prompt)
		if user_input.strip() and (not history or user_input != history[-1]):
			history.append(user_input)
			config['command_history'] = history
			save_user_config(config)
		return user_input


def get_llm(config: dict[str, Any]):
	"""Get the language model based on config and available API keys."""
	# Set API keys from config if available
	api_keys = config.get('model', {}).get('api_keys', {})
	model_name = config.get('model', {}).get('name')
	temperature = config.get('model', {}).get('temperature', 0.0)

	# Set environment variables if they're in the config but not in the environment
	if api_keys.get('openai') and not os.getenv('OPENAI_API_KEY'):
		os.environ['OPENAI_API_KEY'] = api_keys['openai']
	if api_keys.get('anthropic') and not os.getenv('ANTHROPIC_API_KEY'):
		os.environ['ANTHROPIC_API_KEY'] = api_keys['anthropic']
	if api_keys.get('google') and not os.getenv('GOOGLE_API_KEY'):
		os.environ['GOOGLE_API_KEY'] = api_keys['google']

	if model_name:
		if model_name.startswith('gpt'):
			if not os.getenv('OPENAI_API_KEY'):
				print('⚠️  OpenAI API key not found. Please update your config or set OPENAI_API_KEY environment variable.')
				sys.exit(1)
			return langchain_openai.ChatOpenAI(model=model_name, temperature=temperature)
		elif model_name.startswith('claude'):
			if not os.getenv('ANTHROPIC_API_KEY'):
				print('⚠️  Anthropic API key not found. Please update your config or set ANTHROPIC_API_KEY environment variable.')
				sys.exit(1)
			return langchain_anthropic.ChatAnthropic(model=model_name, temperature=temperature)
		elif model_name.startswith('gemini'):
			if not os.getenv('GOOGLE_API_KEY'):
				print('⚠️  Google API key not found. Please update your config or set GOOGLE_API_KEY environment variable.')
				sys.exit(1)
			return langchain_google_genai.ChatGoogleGenerativeAI(model=model_name, temperature=temperature)

	# Auto-detect based on available API keys
	if os.getenv('OPENAI_API_KEY'):
		return langchain_openai.ChatOpenAI(model='gpt-4o', temperature=temperature)
	elif os.getenv('ANTHROPIC_API_KEY'):
		return langchain_anthropic.ChatAnthropic(model='claude-3-opus-20240229', temperature=temperature)
	elif os.getenv('GOOGLE_API_KEY'):
		return langchain_google_genai.ChatGoogleGenerativeAI(model='gemini-pro', temperature=temperature)
	else:
		print(
			'⚠️  No API keys found. Please update your config or set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY.'
		)
		sys.exit(1)


def parse_arguments():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(description='Browser-Use Interactive CLI')
	parser.add_argument('--model', type=str, help='Model to use (e.g., gpt-4o, claude-3-opus-20240229, gemini-pro)')
	parser.add_argument('--headless', action='store_true', help='Run browser in headless mode', default=None)
	parser.add_argument('--no-headless', dest='headless', action='store_false', help='Run browser in visible mode')
	parser.add_argument('--window-width', type=int, help='Browser window width')
	parser.add_argument('--window-height', type=int, help='Browser window height')

	return parser.parse_args()


async def interactive_session():
	"""Run an interactive browser-use session."""
	load_dotenv()

	# Parse command-line arguments
	args = parse_arguments()

	# Load user configuration
	config = load_user_config()

	# Update config with command-line arguments
	config = update_config_with_args(config, args)

	# Save updated config
	save_user_config(config)

	print(LOGO)

	# Print config file hint message in darker grey
	homepath = str(Path('~').expanduser())
	config_relpath = str(USER_CONFIG_FILE.resolve()).replace(homepath, '~')
	output_relpath = str(Path('.').resolve()).replace(homepath, '~')
	print(f'\033[38;5;240m⚙️ Settings & history will be saved to:   \033[38;5;270m  {config_relpath}\033[0m')
	print(f'\033[38;5;240m📁 Outputs & recordings will be saved to: \033[38;5;270m  {output_relpath}\033[0m')
	print()

	# Configure components from JSON directly
	browser_config = BrowserConfig.model_validate(config.get('browser', {}))
	context_config = BrowserContextConfig.model_validate(config.get('browser_context', {}))

	# Set context config in browser config
	browser_config.new_context_config = context_config

	browser = Browser(config=browser_config)
	controller = Controller()

	# Get LLM with config
	llm = get_llm(config)

	# First task
	print('🔍 What would you like me to do on the web?')
	try:
		task = get_user_input('\033[38;5;208m>\033[0m  ', config)
		print()
	except (KeyboardInterrupt, EOFError):
		print('\n\n👋 \033[38;5;240m Goodbye! Browser-Use session ended.\033[0m')
		return

	# Create agent with user settings from JSON
	agent_settings = AgentSettings.model_validate(config.get('agent', {}))

	agent = Agent(task=task, llm=llm, controller=controller, browser=browser, config=agent_settings)

	try:
		while True:
			print('\n🚀 Working on it...\n')
			await agent.run()

			print('\n✅ Task completed! Results above.')
			print('\n📝 Enter a new task or press Ctrl+C to exit:')
			try:
				new_task = get_user_input('\033[38;5;208m>\033[0m  ', config)
				print()
			except (KeyboardInterrupt, EOFError):
				print('\n\n👋 \033[38;5;240m Goodbye! Browser-Use session ended.\033[0m')
				return

			if not new_task.strip():
				continue

			agent.add_new_task(new_task)

	except KeyboardInterrupt:
		print('\n\n👋 Goodbye! Browser-Use session ended.')
	finally:
		# Clean up resources
		await browser.close()


def main():
	"""CLI entry point."""
	asyncio.run(interactive_session())


if __name__ == '__main__':
	main()
