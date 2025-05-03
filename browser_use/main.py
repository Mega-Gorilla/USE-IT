import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import click
import langchain_anthropic
import langchain_google_genai
import langchain_openai
from dotenv import load_dotenv
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, HorizontalGroup, VerticalScroll
from textual.widgets import Footer, Header, Input, Label, Link, RichLog, Static

try:
	import readline

	READLINE_AVAILABLE = True
except ImportError:
	# readline not available on Windows by default
	READLINE_AVAILABLE = False

from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig, Controller
from browser_use.agent.views import AgentSettings
from browser_use.logging_config import addLoggingLevel

# User settings file
USER_CONFIG_FILE = Path.home() / '.browser_use.json'
MAX_HISTORY_LENGTH = 100

# Logo components with styling for rich panels
BROWSER_LOGO = """
                                  [white]   ++++++   +++++++++   [/]                                
                                  [white] +++     +++++     +++  [/]                                
                                  [white] ++    ++++   ++    ++  [/]                                
                                  [white] ++  +++       +++  ++  [/]                                
                                  [white]   ++++          +++    [/]                                
                                  [white]  +++             +++   [/]                                
                                  [white] +++               +++  [/]                                
                                  [white] ++   +++      +++  ++  [/]                                
                                  [white] ++    ++++   ++    ++  [/]                                
                                  [white] +++     ++++++    +++  [/]                                
                                  [white]   ++++++    +++++++    [/]                                

[white]██████╗ ██████╗  ██████╗ ██╗    ██╗███████╗███████╗██████╗[/]     [darkorange]██╗   ██╗███████╗███████╗[/]
[white]██╔══██╗██╔══██╗██╔═══██╗██║    ██║██╔════╝██╔════╝██╔══██╗[/]    [darkorange]██║   ██║██╔════╝██╔════╝[/]
[white]██████╔╝██████╔╝██║   ██║██║ █╗ ██║███████╗█████╗  ██████╔╝[/]    [darkorange]██║   ██║███████╗█████╗[/]  
[white]██╔══██╗██╔══██╗██║   ██║██║███╗██║╚════██║██╔══╝  ██╔══██╗[/]    [darkorange]██║   ██║╚════██║██╔══╝[/]  
[white]██████╔╝██║  ██║╚██████╔╝╚███╔███╔╝███████║███████╗██║  ██║[/]    [darkorange]╚██████╔╝███████║███████╗[/]
[white]╚═════╝ ╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝ ╚══════╝╚══════╝╚═╝  ╚═╝[/]     [darkorange]╚═════╝ ╚══════╝╚══════╝[/]
"""


# Common UI constants
TEXTUAL_BORDER_STYLES = {'logo': 'blue', 'info': 'blue', 'input': 'orange3', 'working': 'yellow', 'completion': 'green'}


def get_default_config() -> dict[str, Any]:
	"""Return default configuration dictionary."""
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
		'agent': {},  # AgentSettings will use defaults
		'browser': {
			'headless': False,
		},
		'browser_context': {
			'keep_alive': True,
			'ignore_https_errors': False,
		},
		'command_history': [],
	}


def load_user_config() -> dict[str, Any]:
	"""Load user configuration from file."""
	if not USER_CONFIG_FILE.exists():
		# Create default config
		config = get_default_config()
		save_user_config(config)
		return config

	try:
		with open(USER_CONFIG_FILE) as f:
			data = json.load(f)
			# Ensure data is a dictionary, not a list
			if isinstance(data, list):
				# If it's a list, it's probably just command history from previous version
				config = get_default_config()
				config['command_history'] = data  # Use the list as command history
				return config
			return data
	except (json.JSONDecodeError, FileNotFoundError):
		# If file is corrupted, start with empty config
		return get_default_config()


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


def update_config_with_click_args(config: dict[str, Any], ctx: click.Context) -> dict[str, Any]:
	"""Update configuration with command-line arguments."""
	# Ensure required sections exist
	if 'model' not in config:
		config['model'] = {}
	if 'browser' not in config:
		config['browser'] = {}
	if 'browser_context' not in config:
		config['browser_context'] = {}

	# Update configuration with command-line args if provided
	if ctx.params.get('model'):
		config['model']['name'] = ctx.params['model']
	if ctx.params.get('headless') is not None:
		config['browser']['headless'] = ctx.params['headless']
	if ctx.params.get('window_width'):
		config['browser']['window_width'] = ctx.params['window_width']
		if 'viewport_width' in config['browser_context']:
			config['browser_context']['viewport_width'] = ctx.params['window_width']
	if ctx.params.get('window_height'):
		config['browser']['window_height'] = ctx.params['window_height']
		if 'viewport_height' in config['browser_context']:
			config['browser_context']['viewport_height'] = ctx.params['window_height']

	return config


def setup_readline_history(history: list[str]) -> None:
	"""Set up readline with command history."""
	if not READLINE_AVAILABLE:
		return

	# Add history items to readline
	for item in history:
		readline.add_history(item)


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
		return langchain_anthropic.ChatAnthropic(model='claude-3-sonnet-20240229', temperature=temperature)
	elif os.getenv('GOOGLE_API_KEY'):
		return langchain_google_genai.ChatGoogleGenerativeAI(model='gemini-pro', temperature=temperature)
	else:
		print(
			'⚠️  No API keys found. Please update your config or set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY.'
		)
		sys.exit(1)


class RichLogHandler(logging.Handler):
	"""Custom logging handler that redirects logs to a RichLog widget."""

	def __init__(self, rich_log: RichLog):
		super().__init__()
		self.rich_log = rich_log

	def emit(self, record):
		try:
			msg = self.format(record)
			self.rich_log.write(msg)
		except Exception:
			self.handleError(record)


class BrowserUseApp(App):
	"""Browser-use TUI application."""

	# Make it an inline app instead of fullscreen
	# MODES = {"light"}  # Ensure app is inline, not fullscreen

	CSS = """
    #main-container {
        height: 100%;
        layout: vertical;
    }
    
    #logo-panel, #links-panel, #paths-panel {
        border: solid $primary;
        margin: 0 0 1 0; 
        padding: 0;
    }
    
    #logo-panel {
        width: 100%;
        height: auto;
        content-align: center middle;
        text-align: center;
    }
    
    #links-panel {
        width: 100%;
        padding: 1;
        border: solid $primary;
        height: auto;
    }
    
    .link-white {
        color: white;
    }
    
    .link-purple {
        color: purple;
    }
    
    .link-magenta {
        color: magenta;
    }
    
    .link-green {
        color: green;
    }

    HorizontalGroup {
        height: auto;
    }
    
    .link-label {
        width: auto;
    }
    
    .link-url {
        width: auto;
    }
    
    .link-row {
        width: 100%;
        height: auto;
    }
    
    #paths-panel {
        color: $text-muted;
    }
    
    #task-input-container {
        border: solid $accent;
        padding: 1;
        margin-bottom: 1;
        height: auto;
    }
    
    #task-label {
        color: $accent;
        padding-bottom: 1;
    }
    
    #task-input {
        width: 100%;
    }
    
    #working-panel {
        border: solid $warning;
        padding: 1;
        margin: 1 0;
    }
    
    #completion-panel {
        border: solid $success;
        padding: 1;
        margin: 1 0;
    }
    
    #results-container {
        height: 1fr;
        overflow: auto;
        border: none;
    }
    
    #results-log {
        height: auto;
        overflow-y: scroll;
        background: $surface;
        color: $text;
        width: 100%;
    }
    
    .log-entry {
        margin: 0;
        padding: 0;
    }
    """

	BINDINGS = [
		Binding('ctrl+c', 'quit', 'Quit', priority=True, show=True),
		Binding('ctrl+q', 'quit', 'Quit', priority=True),
		Binding('ctrl+d', 'quit', 'Quit', priority=True),
		Binding('up', 'input_history_prev', 'Previous command', show=False),
		Binding('down', 'input_history_next', 'Next command', show=False),
	]

	def __init__(self, config: dict[str, Any], *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.config = config
		self.browser = None
		self.controller = None
		self.agent = None
		self.llm = None
		self.task_history = config.get('command_history', [])
		# Track current position in history for up/down navigation
		self.history_index = len(self.task_history)

	def setup_richlog_logging(self) -> None:
		"""Set up logging to redirect to RichLog widget instead of stdout."""
		# Try to add RESULT level if it doesn't exist
		try:
			addLoggingLevel('RESULT', 35)
		except AttributeError:
			pass  # Level already exists, which is fine

		# Get the RichLog widget
		rich_log = self.query_one('#results-log')

		# Create and set up the custom handler
		log_handler = RichLogHandler(rich_log)
		log_type = os.getenv('BROWSER_USE_LOGGING_LEVEL', 'info').lower()

		class BrowserUseFormatter(logging.Formatter):
			def format(self, record):
				if isinstance(record.name, str) and record.name.startswith('browser_use.'):
					record.name = record.name.split('.')[-2]
				return super().format(record)

		# Set up the formatter based on log type
		if log_type == 'result':
			log_handler.setLevel('RESULT')
			log_handler.setFormatter(BrowserUseFormatter('%(message)s'))
		else:
			log_handler.setFormatter(BrowserUseFormatter('%(levelname)-8s [%(name)s] %(message)s'))

		# Configure root logger
		root = logging.getLogger()

		# Remove any existing handlers that write to stdout
		for handler in root.handlers[:]:
			if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
				root.removeHandler(handler)

		# Add our custom handler
		root.addHandler(log_handler)

		# Set log level based on environment variable
		if log_type == 'result':
			root.setLevel('RESULT')
		elif log_type == 'debug':
			root.setLevel(logging.DEBUG)
		else:
			root.setLevel(logging.INFO)

		# Configure browser_use logger
		browser_use_logger = logging.getLogger('browser_use')
		browser_use_logger.propagate = False  # Don't propagate to root logger
		browser_use_logger.addHandler(log_handler)
		browser_use_logger.setLevel(root.level)

		# Silence third-party loggers
		for logger_name in [
			'WDM',
			'httpx',
			'selenium',
			'playwright',
			'urllib3',
			'asyncio',
			'langchain',
			'openai',
			'httpcore',
			'charset_normalizer',
			'anthropic._base_client',
			'PIL.PngImagePlugin',
			'trafilatura.htmlprocessing',
			'trafilatura',
		]:
			third_party = logging.getLogger(logger_name)
			third_party.setLevel(logging.ERROR)
			third_party.propagate = False

	def on_mount(self) -> None:
		"""Set up components when app is mounted."""
		# Configure BrowserUse components
		browser_config = BrowserConfig.model_validate(self.config.get('browser', {}))
		context_config = BrowserContextConfig.model_validate(self.config.get('browser_context', {}))
		browser_config.new_context_config = context_config

		self.browser = Browser(config=browser_config)
		self.controller = Controller()
		self.llm = get_llm(self.config)

		# Set up custom logging to RichLog (must be done after UI is mounted)
		self.setup_richlog_logging()

		# Set up input history if available
		if READLINE_AVAILABLE and self.task_history:
			for item in self.task_history:
				readline.add_history(item)

		# Focus the input field
		input_field = self.query_one('#task-input')
		input_field.focus()

	def on_input_key_up(self, event: events.Key) -> None:
		"""Handle up arrow key in the input field."""
		# Check if event is from the input field
		if event.sender.id != 'task-input':
			return

		# Only process if we have history
		if not self.task_history:
			return

		# Move back in history if possible
		if self.history_index > 0:
			self.history_index -= 1
			self.query_one('#task-input').value = self.task_history[self.history_index]
			# Move cursor to end of text
			self.query_one('#task-input').cursor_position = len(self.query_one('#task-input').value)

		# Prevent default behavior (cursor movement)
		event.prevent_default()
		event.stop()

	def on_input_key_down(self, event: events.Key) -> None:
		"""Handle down arrow key in the input field."""
		# Check if event is from the input field
		if event.sender.id != 'task-input':
			return

		# Only process if we have history
		if not self.task_history:
			return

		# Move forward in history or clear input if at the end
		if self.history_index < len(self.task_history) - 1:
			self.history_index += 1
			self.query_one('#task-input').value = self.task_history[self.history_index]
			# Move cursor to end of text
			self.query_one('#task-input').cursor_position = len(self.query_one('#task-input').value)
		elif self.history_index == len(self.task_history) - 1:
			# At the end of history, go to "new line" state
			self.history_index += 1
			self.query_one('#task-input').value = ''

		# Prevent default behavior (cursor movement)
		event.prevent_default()
		event.stop()

	async def on_key(self, event: events.Key) -> None:
		"""Handle key events at the app level to ensure graceful exit."""
		# Handle Ctrl+C, Ctrl+D, and Ctrl+Q for app exit
		if event.key == 'ctrl+c' or event.key == 'ctrl+d' or event.key == 'ctrl+q':
			await self.action_quit()
			event.stop()
			event.prevent_default()

	def on_input_submitted(self, event: Input.Submitted) -> None:
		"""Handle task input submission."""
		if event.input.id == 'task-input':
			task = event.input.value
			if not task.strip():
				return

			# Add to history if it's new
			if task.strip() and (not self.task_history or task != self.task_history[-1]):
				self.task_history.append(task)
				self.config['command_history'] = self.task_history
				save_user_config(self.config)

			# Reset history index to point past the end of history
			self.history_index = len(self.task_history)

			# Hide logo, links, and paths panels
			self.hide_intro_panels()

			# Process the task
			self.run_task(task)

			# Clear the input
			event.input.value = ''

	def hide_intro_panels(self) -> None:
		"""Hide the intro panels and expand the log view."""
		# Get the panels
		logo_panel = self.query_one('#logo-panel')
		links_panel = self.query_one('#links-panel')
		paths_panel = self.query_one('#paths-panel')

		# Hide them if they're visible
		if logo_panel.display:
			logo_panel.display = False
			links_panel.display = False
			paths_panel.display = False

			# Make results container take full height
			results_container = self.query_one('#results-container')
			results_container.styles.height = '1fr'

			# Configure the log
			results_log = self.query_one('#results-log')
			results_log.styles.height = 'auto'

	def run_task(self, task: str) -> None:
		"""Launch the task in a background worker."""
		# Create or update the agent
		agent_settings = AgentSettings.model_validate(self.config.get('agent', {}))

		# Get the logger
		logger = logging.getLogger('browser_use.app')

		# Make sure intro is hidden and log is ready
		self.hide_intro_panels()

		# Clear the log to start fresh
		rich_log = self.query_one('#results-log')
		rich_log.clear()

		if self.agent is None:
			self.agent = Agent(
				task=task,
				llm=self.llm,
				controller=self.controller,
				browser=self.browser,
				**agent_settings.model_dump(),
			)
		else:
			self.agent.add_new_task(task)

		# Let the agent run in the background
		async def agent_task_worker() -> None:
			logger.info('\n🚀 Working on task: %s', task)

			try:
				# Run the agent task, redirecting output to RichLog through our handler
				await self.agent.run()
			except Exception as e:
				logger.error('\nError running agent: %s', str(e))
			finally:
				logger.info('\n✅ Task completed!')
				# Refocus the input field
				self.query_one('#task-input').focus()

		# Run the worker
		self.run_worker(agent_task_worker, name='agent_task')

	def action_input_history_prev(self) -> None:
		"""Navigate to the previous item in command history."""
		# Only process if we have history and input is focused
		input_field = self.query_one('#task-input')
		if not input_field.has_focus or not self.task_history:
			return

		# Move back in history if possible
		if self.history_index > 0:
			self.history_index -= 1
			input_field.value = self.task_history[self.history_index]
			# Move cursor to end of text
			input_field.cursor_position = len(input_field.value)

	def action_input_history_next(self) -> None:
		"""Navigate to the next item in command history or clear input."""
		# Only process if we have history and input is focused
		input_field = self.query_one('#task-input')
		if not input_field.has_focus or not self.task_history:
			return

		# Move forward in history or clear input if at the end
		if self.history_index < len(self.task_history) - 1:
			self.history_index += 1
			input_field.value = self.task_history[self.history_index]
			# Move cursor to end of text
			input_field.cursor_position = len(input_field.value)
		elif self.history_index == len(self.task_history) - 1:
			# At the end of history, go to "new line" state
			self.history_index += 1
			input_field.value = ''

	async def action_quit(self) -> None:
		"""Quit the application and clean up resources."""
		if self.browser:
			await self.browser.close()
		self.exit()

	def compose(self) -> ComposeResult:
		"""Create the UI layout."""
		yield Header()

		# Main container for app content
		with Container(id='main-container'):
			# Logo panel
			yield Static(BROWSER_LOGO, id='logo-panel', markup=True)

			# Links panel with URLs
			with Container(id='links-panel'):
				with HorizontalGroup(classes='link-row'):
					yield Static('Run at scale on cloud:    [blink]☁️[/]  ', markup=True, classes='link-label')
					yield Link('https://browser-use.com', url='https://browser-use.com', classes='link-white link-url')

				yield Static('')  # Empty line

				with HorizontalGroup(classes='link-row'):
					yield Static('Chat & share on Discord:  🚀 ', markup=True, classes='link-label')
					yield Link(
						'https://discord.gg/ESAUZAdxXY', url='https://discord.gg/ESAUZAdxXY', classes='link-purple link-url'
					)

				with HorizontalGroup(classes='link-row'):
					yield Static('Get prompt inspiration:   🦸 ', markup=True, classes='link-label')
					yield Link(
						'https://github.com/browser-use/awesome-prompts',
						url='https://github.com/browser-use/awesome-prompts',
						classes='link-magenta link-url',
					)

				with HorizontalGroup(classes='link-row'):
					yield Static('[dim]Report any issues:[/]        🐛 ', markup=True, classes='link-label')
					yield Link(
						'https://github.com/browser-use/browser-use/issues',
						url='https://github.com/browser-use/browser-use/issues',
						classes='link-green link-url',
					)

			# Paths panel
			yield Static(
				f' ⚙️  Settings & history saved to:    {str(USER_CONFIG_FILE.resolve()).replace(str(Path.home()), "~")}\n'
				f' 📁 Outputs & recordings saved to:  {str(Path(".").resolve()).replace(str(Path.home()), "~")}',
				id='paths-panel',
				markup=True,
			)

			# Results view with scrolling (place this before input to make input sticky at bottom)
			with VerticalScroll(id='results-container'):
				yield RichLog(highlight=True, markup=True, id='results-log', wrap=True, auto_scroll=True)

			# Task input container (now at the bottom)
			with Container(id='task-input-container'):
				yield Label('🔍 What would you like me to do on the web?', id='task-label')
				yield Input(placeholder='Enter your task...', id='task-input')

		yield Footer()


async def textual_interface(config: dict[str, Any]):
	"""Run the Textual interface."""
	app = BrowserUseApp(config)
	await app.run_async()


@click.command()
@click.option('--model', type=str, help='Model to use (e.g., gpt-4o, claude-3-opus-20240229, gemini-pro)')
@click.pass_context
def main(ctx: click.Context, **kwargs):
	"""Browser-Use Interactive TUI"""
	load_dotenv()

	# Create a no-op handler to prevent any logging to stdout
	# This will be replaced with our RichLog handler once the app is mounted
	null_handler = logging.NullHandler()
	logging.getLogger().addHandler(null_handler)

	# We're skipping the default setup_logging() which writes to sys.stdout

	# Load user configuration
	config = load_user_config()

	# Update config with command-line arguments
	config = update_config_with_click_args(config, ctx)

	# Save updated config
	save_user_config(config)

	# Run the Textual UI interface
	asyncio.run(textual_interface(config))


if __name__ == '__main__':
	main()
