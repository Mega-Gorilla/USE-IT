import asyncio
import gc
import inspect
import json
import logging
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from dotenv import load_dotenv

from browser_use.llm.base import BaseChatModel
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.messages import ContentPartImageParam, ContentPartTextParam
from browser_use.tokens.service import TokenCost

load_dotenv()

from bubus import EventBus
from uuid_extensions import uuid7str

from browser_use import Browser, BrowserProfile, BrowserSession

# 起動時に重い agent.views の読み込みを避けるため GIF のインポートは遅延させる
# （参考） browser_use.agent.gif から create_history_gif をインポートするサンプル  # 遅延インポート例
from browser_use.agent.config import AgentConfig
from browser_use.agent.filesystem_manager import FilesystemManager
from browser_use.agent.history_manager import HistoryManager
from browser_use.agent.llm_handler import LLMHandler
from browser_use.agent.message_manager.service import MessageManager
from browser_use.agent.pause_controller import PauseController
from browser_use.agent.prompt import SystemPrompt
from browser_use.agent.runner import AgentRunner
from browser_use.agent.step_executor import StepExecutor
from browser_use.agent.telemetry import TelemetryHandler
from browser_use.agent.views import (
	ActionResult,
	AgentHistoryList,
	AgentOutput,
	AgentSettings,
	AgentState,
	AgentStepInfo,
	AgentStructuredOutput,
)
from browser_use.browser.session import DEFAULT_BROWSER_PROFILE
from browser_use.browser.views import BrowserStateSummary
from browser_use.config import CONFIG
from browser_use.filesystem.file_system import FileSystem
from browser_use.observability import observe
from browser_use.sync import CloudSync
from browser_use.tools.registry.views import ActionModel
from browser_use.tools.service import Tools
from browser_use.utils import (
	_log_pretty_path,
	get_browser_use_version,
	get_git_info,
	time_execution_async,
	time_execution_sync,
)

logger = logging.getLogger(__name__)


Context = TypeVar('Context')


AgentHookFunc = Callable[['Agent'], Awaitable[None]]


class Agent(Generic[Context, AgentStructuredOutput]):
	@time_execution_sync('--init')
	def __init__(
		self,
		task: str | None = None,
		llm: BaseChatModel | None = None,
		# 任意パラメータ
		browser_profile: BrowserProfile | None = None,
		browser_session: BrowserSession | None = None,
		browser: Browser | None = None,  # browser_session のエイリアス
		tools: Tools[Context] | None = None,
		controller: Tools[Context] | None = None,  # tools のエイリアス
		# Agent 初回実行時のパラメータ
		sensitive_data: dict[str, str | dict[str, str]] | None = None,
		initial_actions: list[dict[str, dict[str, Any]]] | None = None,
		# クラウド連携用コールバック
		register_new_step_callback: (
			Callable[['BrowserStateSummary', 'AgentOutput', int], None]  # 同期コールバック
			| Callable[['BrowserStateSummary', 'AgentOutput', int], Awaitable[None]]  # 非同期コールバック
			| None
		) = None,
		register_done_callback: (
			Callable[['AgentHistoryList'], Awaitable[None]]  # 非同期コールバック
			| Callable[['AgentHistoryList'], None]  # 同期コールバック
			| None
		) = None,
		register_external_agent_status_raise_error_callback: Callable[[], Awaitable[bool]] | None = None,
		register_should_stop_callback: Callable[[], Awaitable[bool]] | None = None,
		# Agent 設定
		output_model_schema: type[AgentStructuredOutput] | None = None,
		use_vision: bool | Literal['auto'] = 'auto',
		save_conversation_path: str | Path | None = None,
		save_conversation_path_encoding: str | None = 'utf-8',
		max_failures: int = 3,
		override_system_message: str | None = None,
		extend_system_message: str | None = None,
		generate_gif: bool | str = False,
		available_file_paths: list[str] | None = None,
		include_attributes: list[str] | None = None,
		max_actions_per_step: int = 10,
		use_thinking: bool = True,
		flash_mode: bool = False,
		max_history_items: int | None = None,
		page_extraction_llm: BaseChatModel | None = None,
		injected_agent_state: AgentState | None = None,
		source: str | None = None,
		file_system_path: str | None = None,
		task_id: str | None = None,
		cloud_sync: CloudSync | None = None,
		calculate_cost: bool = False,
		display_files_in_done_text: bool = True,
		include_tool_call_examples: bool = False,
		vision_detail_level: Literal['auto', 'low', 'high'] = 'auto',
		llm_timeout: int | None = None,
		step_timeout: int = 120,
		directly_open_url: bool = True,
		include_recent_events: bool = False,
		sample_images: list[ContentPartTextParam | ContentPartImageParam] | None = None,
		final_response_after_failure: bool = True,
		_url_shortening_limit: int = 25,
		config: AgentConfig | None = None,
		**kwargs,
	):
		if config is None:
			if task is None:
				raise ValueError('task is required when config is not provided.')
			config = AgentConfig(
				task=task,
				llm=llm,
				browser_profile=browser_profile,
				browser_session=browser_session,
				browser=browser,
				tools=tools,
				controller=controller,
				sensitive_data=sensitive_data,
				initial_actions=initial_actions,
				register_new_step_callback=register_new_step_callback,
				register_done_callback=register_done_callback,
				register_external_agent_status_raise_error_callback=register_external_agent_status_raise_error_callback,
				register_should_stop_callback=register_should_stop_callback,
				output_model_schema=output_model_schema,
				use_vision=use_vision,
				save_conversation_path=save_conversation_path,
				save_conversation_path_encoding=save_conversation_path_encoding,
				max_failures=max_failures,
				override_system_message=override_system_message,
				extend_system_message=extend_system_message,
				generate_gif=generate_gif,
				available_file_paths=available_file_paths,
				include_attributes=include_attributes,
				max_actions_per_step=max_actions_per_step,
				use_thinking=use_thinking,
				flash_mode=flash_mode,
				max_history_items=max_history_items,
				page_extraction_llm=page_extraction_llm,
				injected_agent_state=injected_agent_state,
				source=source,
				file_system_path=file_system_path,
				task_id=task_id,
				cloud_sync=cloud_sync,
				calculate_cost=calculate_cost,
				display_files_in_done_text=display_files_in_done_text,
				include_tool_call_examples=include_tool_call_examples,
				vision_detail_level=vision_detail_level,
				llm_timeout=llm_timeout,
				step_timeout=step_timeout,
				directly_open_url=directly_open_url,
				include_recent_events=include_recent_events,
				sample_images=sample_images,
				final_response_after_failure=final_response_after_failure,
				url_shortening_limit=_url_shortening_limit,
				extra=kwargs,
			)  # type: ignore[call-arg]
		else:
			# config 優先、追加の kwargs は余白に詰める
			if kwargs:
				config.extra.update(kwargs)

		self.config = config
		cfg = config
		self.factories = cfg.factories

		self.task = cfg.task
		self.llm, page_extraction_llm, flash_mode, llm_timeout, initial_paths = self._resolve_defaults(
			cfg.llm,
			cfg.page_extraction_llm,
			cfg.flash_mode,
			cfg.available_file_paths,
			cfg.llm_timeout,
		)
		self._initial_available_file_paths = initial_paths
		self.id = cfg.task_id or uuid7str()
		self.task_id = self.id
		self.session_id = uuid7str()

		browser_profile = cfg.browser_profile or DEFAULT_BROWSER_PROFILE
		if cfg.browser and cfg.browser_session:
			raise ValueError('Cannot specify both "browser" and "browser_session" parameters. Use "browser" for the cleaner API.')

		browser_session = cfg.browser or cfg.browser_session
		self.browser_session = browser_session or BrowserSession(
			browser_profile=browser_profile,
			id=uuid7str()[:-4] + self.id[-4:],
		)

		self.filesystem_manager: FilesystemManager | None = None
		self.directly_open_url = cfg.directly_open_url
		self.include_recent_events = cfg.include_recent_events
		self._url_shortening_limit = cfg.url_shortening_limit

		self.tools = self._prepare_tools(cfg.tools, cfg.controller, cfg.use_vision, cfg.display_files_in_done_text)
		self.output_model_schema = cfg.output_model_schema
		if self.output_model_schema is not None:
			self.tools.use_structured_output_action(self.output_model_schema)

		self.sensitive_data = cfg.sensitive_data
		self.sample_images = cfg.sample_images

		self.settings = self._build_settings(
			use_vision=cfg.use_vision,
			vision_detail_level=cfg.vision_detail_level,
			save_conversation_path=cfg.save_conversation_path,
			save_conversation_path_encoding=cfg.save_conversation_path_encoding,
			max_failures=cfg.max_failures,
			override_system_message=cfg.override_system_message,
			extend_system_message=cfg.extend_system_message,
			generate_gif=cfg.generate_gif,
			include_attributes=cfg.include_attributes,
			max_actions_per_step=cfg.max_actions_per_step,
			use_thinking=cfg.use_thinking,
			flash_mode=flash_mode,
			max_history_items=cfg.max_history_items,
			page_extraction_llm=page_extraction_llm,
			calculate_cost=cfg.calculate_cost,
			include_tool_call_examples=cfg.include_tool_call_examples,
			llm_timeout=llm_timeout,
			step_timeout=cfg.step_timeout,
			final_response_after_failure=cfg.final_response_after_failure,
		)

		self._initialize_token_cost_service(cfg.calculate_cost, self.llm, page_extraction_llm)
		self._initialize_history_components(cfg.injected_agent_state)
		self._initialize_filesystem(cfg.file_system_path)

		self._setup_action_models()
		self._set_browser_use_version_and_source(cfg.source)

		initial_url = None

		# 初期アクションが無い場合に限り URL 自動ロードを行う
		if self.directly_open_url and not self.state.follow_up_task and not cfg.initial_actions:
			initial_url = self._extract_url_from_task(self.task)
			if initial_url:
				self.logger.info(f'🔗 Found URL in task: {initial_url}, adding as initial action...')
				cfg.initial_actions = [{'navigate': {'url': initial_url, 'new_tab': False}}]

		self.initial_url = initial_url

		self.initial_actions = (
			self._convert_initial_actions(cfg.initial_actions) if cfg.initial_actions else None
		)
		# モデル接続と設定を確認する
		self._verify_and_setup_llm()

		# TODO: この判定は将来的に LLM 実装側へ移す
		# DeepSeek 系モデルで use_vision=True が指定された場合の警告
		if 'deepseek' in self.llm.model.lower():
			self.logger.warning('⚠️ DeepSeek models do not support use_vision=True yet. Setting use_vision=False for now...')
			self.settings.use_vision = False

		# XAI(Grok) 系モデルで use_vision=True が指定された場合の警告
		if 'grok' in self.llm.model.lower():
			self.logger.warning('⚠️ XAI models do not support use_vision=True yet. Setting use_vision=False for now...')
			self.settings.use_vision = False

		logger.debug(
			f'{" +vision" if self.settings.use_vision else ""}'
			f' extraction_model={self.settings.page_extraction_llm.model if self.settings.page_extraction_llm else "Unknown"}'
			f'{" +file_system" if self.file_system else ""}'
		)

		# MessageManager を状態付きで初期化
		# 初期システムプロンプトには全アクションが含まれ、各ステップで更新される
		self._message_manager = MessageManager(
			task=cfg.task,
			system_message=SystemPrompt(
				max_actions_per_step=self.settings.max_actions_per_step,
				override_system_message=cfg.override_system_message,
				extend_system_message=cfg.extend_system_message,
				use_thinking=self.settings.use_thinking,
				flash_mode=self.settings.flash_mode,
			).get_system_message(),
			file_system=self.file_system,
			state=self.state.message_manager_state,
			use_thinking=self.settings.use_thinking,
			# 以前 MessageManagerSettings にあったパラメータ
			include_attributes=self.settings.include_attributes,
			sensitive_data=self.sensitive_data,
			max_history_items=self.settings.max_history_items,
			vision_detail_level=self.settings.vision_detail_level,
			include_tool_call_examples=self.settings.include_tool_call_examples,
			include_recent_events=self.include_recent_events,
			sample_images=self.sample_images,
		)

		if self.sensitive_data:
			has_domain_specific_credentials = any(isinstance(v, dict) for v in self.sensitive_data.values())

			if not self.browser_profile.allowed_domains:
				self.logger.error(
					'⚠️ Agent(sensitive_data=••••••••) was provided but Browser(allowed_domains=[...]) is not locked down! ⚠️\n'
					'          ☠️ If the agent visits a malicious website and encounters a prompt-injection attack, your sensitive_data may be exposed!\n\n'
					'   \n'
				)
			elif has_domain_specific_credentials:
				domain_patterns = [k for k, v in self.sensitive_data.items() if isinstance(v, dict)]

				for domain_pattern in domain_patterns:
					is_allowed = False
					for allowed_domain in self.browser_profile.allowed_domains:
						if domain_pattern == allowed_domain or allowed_domain == '*':
							is_allowed = True
							break

						pattern_domain = domain_pattern.split('://')[-1] if '://' in domain_pattern else domain_pattern
						allowed_domain_part = (
							allowed_domain.split('://')[-1] if '://' in allowed_domain else allowed_domain
						)

						if pattern_domain == allowed_domain_part or (
							allowed_domain_part.startswith('*.')
							and (
								pattern_domain == allowed_domain_part[2:]
								or pattern_domain.endswith('.' + allowed_domain_part[2:])
							)
						):
							is_allowed = True
							break

				if not is_allowed:
					self.logger.warning(
						f'⚠️ Domain pattern "{domain_pattern}" in sensitive_data is not covered by any pattern in allowed_domains={self.browser_profile.allowed_domains}\n'
						f'   This may be a security risk as credentials could be used on unintended domains.'
					)

		self.register_new_step_callback = cfg.register_new_step_callback
		self.register_done_callback = cfg.register_done_callback
		self.register_should_stop_callback = cfg.register_should_stop_callback
		self.register_external_agent_status_raise_error_callback = cfg.register_external_agent_status_raise_error_callback

		self.telemetry = self.factories.telemetry_factory(self)
		self.telemetry_handler = TelemetryHandler(self)
		self.llm_handler = LLMHandler(self)
		self.step_executor = StepExecutor(self)
		self.pause_controller = PauseController(self.logger)

		self.eventbus = self.factories.event_bus_factory(self)

		self.enable_cloud_sync = CONFIG.BROWSER_USE_CLOUD_SYNC
		if self.enable_cloud_sync or cfg.cloud_sync is not None:
			self.cloud_sync = cfg.cloud_sync if cfg.cloud_sync is not None else self.factories.cloud_sync_factory(self)
			if self.cloud_sync:
				self.eventbus.on('*', self.cloud_sync.handle_event)
		else:
			self.cloud_sync = None

		if self.settings.save_conversation_path:
			self.settings.save_conversation_path = Path(self.settings.save_conversation_path).expanduser().resolve()
			self.logger.info(f'💬 Saving conversation to {_log_pretty_path(self.settings.save_conversation_path)}')


	@property
	def logger(self) -> logging.Logger:
		"""Get instance-specific logger with task ID in the name"""

		_browser_session_id = self.browser_session.id if self.browser_session else '----'
		_current_target_id = (
			self.browser_session.agent_focus.target_id[-2:]
			if self.browser_session and self.browser_session.agent_focus and self.browser_session.agent_focus.target_id
			else '--'
		)
		return logging.getLogger(f'browser_use.Agent🅰 {self.task_id[-4:]} ⇢ 🅑 {_browser_session_id[-4:]} 🅣 {_current_target_id}')

	@property
	def browser_profile(self) -> BrowserProfile:
		assert self.browser_session is not None, 'BrowserSession is not set up'
		return self.browser_session.browser_profile

	@property
	def file_system(self) -> FileSystem | None:
		if not self.filesystem_manager:
			return None
		return self.filesystem_manager.file_system

	@property
	def file_system_path(self) -> str | None:
		if not self.filesystem_manager:
			return None
		return self.filesystem_manager.file_system_path

	@property
	def available_file_paths(self) -> list[str]:
		if self.filesystem_manager:
			return self.filesystem_manager.available_file_paths
		return self._initial_available_file_paths

	@available_file_paths.setter
	def available_file_paths(self, paths: list[str]) -> None:
		if self.filesystem_manager:
			self.filesystem_manager.available_file_paths = list(paths)
		else:
			self._initial_available_file_paths = list(paths)

	async def _check_and_update_downloads(self, context: str = '') -> None:
		"""Check for new downloads and update available file paths."""
		if self.filesystem_manager:
			await self.filesystem_manager.check_and_update_downloads(context)

	def _set_screenshot_service(self) -> None:
		"""Initialize screenshot service using agent directory"""
		try:
			from browser_use.screenshots.service import ScreenshotService

			self.screenshot_service = ScreenshotService(self.agent_directory)
			logger.debug(f'📸 Screenshot service initialized in: {self.agent_directory}/screenshots')
		except Exception as e:
			logger.error(f'📸 Failed to initialize screenshot service: {e}.')
			raise e

	def save_file_system_state(self) -> None:
		"""Save current file system state to agent state"""
		if self.filesystem_manager:
			self.filesystem_manager.save_state()

	def _set_browser_use_version_and_source(self, source_override: str | None = None) -> None:
		"""Get the version from pyproject.toml and determine the source of the browser-use package"""
		# バージョン判定はヘルパー関数に任せる
		version = get_browser_use_version()

		# パッケージの出所を判定
		try:
			package_root = Path(__file__).parent.parent.parent
			repo_files = ['.git', 'README.md', 'docs', 'examples']
			if all(Path(package_root / file).exists() for file in repo_files):
				source = 'git'
			else:
				source = 'pip'
		except Exception as e:
			self.logger.debug(f'Error determining source: {e}')
			source = 'unknown'

		if source_override is not None:
			source = source_override
		# self.logger.debug(f'Version: {version}, Source: {source}')  # サポート用ログに含めてもらいやすいよう _log_agent_run へ移動済み
		self.version = version
		self.source = source

	def _setup_action_models(self) -> None:
		"""Setup dynamic action models from tools registry"""
		# 初期状態ではフィルタなしのアクションのみを含める
		self.ActionModel = self.tools.registry.create_action_model()
		# 動的アクションを反映した出力モデルを生成
		if self.settings.flash_mode:
			self.AgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.ActionModel)
		elif self.settings.use_thinking:
			self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)
		else:
			self.AgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.ActionModel)

		# 最大ステップ到達時に強制的に done を使わせるためのモデル
		self.DoneActionModel = self.tools.registry.create_action_model(include_actions=['done'])
		if self.settings.flash_mode:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.DoneActionModel)
		elif self.settings.use_thinking:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions(self.DoneActionModel)
		else:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.DoneActionModel)

	def _resolve_defaults(
		self,
		llm: BaseChatModel | None,
		page_extraction_llm: BaseChatModel | None,
		flash_mode: bool,
		available_file_paths: list[str] | None,
		llm_timeout: int | None,
	) -> tuple[BaseChatModel, BaseChatModel, bool, int, list[str]]:
		if llm is None:
			default_llm_name = CONFIG.DEFAULT_LLM
			if default_llm_name:
				try:
					from browser_use.llm.models import get_llm_by_name

					llm = get_llm_by_name(default_llm_name)
				except (ImportError, ValueError) as exc:
					logger.warning(
						f'Failed to create default LLM "{default_llm_name}": {exc}. Falling back to ChatGoogle(model="gemini-flash-latest")'
					)
					llm = ChatGoogle(model='gemini-flash-latest')
			else:
				llm = ChatGoogle(model='gemini-flash-latest')

		if llm.provider == 'browser-use':
			flash_mode = True

		if page_extraction_llm is None:
			page_extraction_llm = llm

		initial_paths = list(available_file_paths or [])

		if llm_timeout is None:

			def _get_model_timeout(llm_model: BaseChatModel) -> int:
				model_name = getattr(llm_model, 'model', '').lower()
				if 'gemini' in model_name:
					return 45
				if 'groq' in model_name:
					return 30
				if any(keyword in model_name for keyword in ('o3', 'claude', 'sonnet', 'deepseek')):
					return 90
				return 60

			llm_timeout = _get_model_timeout(llm)

		return llm, page_extraction_llm, flash_mode, llm_timeout, initial_paths

	def _prepare_tools(
		self,
		tools: Tools[Context] | None,
		controller: Tools[Context] | None,
		use_vision: bool | Literal['auto'],
		display_files_in_done_text: bool,
	) -> Tools[Context]:
		if tools is not None:
			return tools
		if controller is not None:
			return controller

		exclude_actions = ['screenshot'] if use_vision is False else []
		return Tools(exclude_actions=exclude_actions, display_files_in_done_text=display_files_in_done_text)

	def _build_settings(
		self,
		**kwargs: Any,
	) -> AgentSettings:
		return AgentSettings(**kwargs)

	def _initialize_token_cost_service(
		self,
		include_cost: bool,
		llm: BaseChatModel,
		page_extraction_llm: BaseChatModel | None,
	) -> None:
		self.token_cost_service = TokenCost(include_cost=include_cost)
		self.token_cost_service.register_llm(llm)
		if page_extraction_llm:
			self.token_cost_service.register_llm(page_extraction_llm)

	def _initialize_history_components(self, injected_agent_state: AgentState | None) -> None:
		self.state = injected_agent_state or AgentState()
		self.history = AgentHistoryList(history=[], usage=None)
		self.history_manager = HistoryManager(self)

	def _initialize_filesystem(self, file_system_path: str | None) -> None:
		timestamp = int(time.time())
		base_tmp = Path(tempfile.gettempdir())
		self.agent_directory = base_tmp / f'browser_use_agent_{self.id}_{timestamp}'

		self.filesystem_manager = FilesystemManager(
			state=self.state,
			browser_session=self.browser_session,
			agent_directory=self.agent_directory,
			available_file_paths=self._initial_available_file_paths,
			file_system_path=file_system_path,
			logger=self.logger,
		)
		self._initial_available_file_paths = []
		self._set_screenshot_service()

	def add_new_task(self, new_task: str) -> None:
		"""Add a new task to the agent, keeping the same task_id as tasks are continuous"""
		# MessageManager に委譲するだけで task_id やイベントを作り直す必要はない
		# タスクは新しい指示で継続する想定で、終了→再開ではない
		self.task = new_task
		self._message_manager.add_new_task(new_task)
		# 後続タスクとして扱い、イベントバスを再生成する（run 後は停止済みのため）
		self.state.follow_up_task = True
		agent_id_suffix = str(self.id)[-4:].replace('-', '_')
		if agent_id_suffix and agent_id_suffix[0].isdigit():
			agent_id_suffix = 'a' + agent_id_suffix
		self.eventbus = EventBus(name=f'Agent_{agent_id_suffix}')

		# クラウド同期が有効な場合はハンドラを再登録
		if hasattr(self, 'cloud_sync') and self.cloud_sync and self.enable_cloud_sync:
			self.eventbus.on('*', self.cloud_sync.handle_event)

	async def _check_stop_or_pause(self) -> None:
		"""Check if the agent should stop or pause, and handle accordingly."""

		# should_stop_callback をチェックし、例外を投げずに停止フラグを立てる
		if self.register_should_stop_callback:
			if await self.register_should_stop_callback():
				self.logger.info('External callback requested stop')
				self.state.stopped = True
				raise InterruptedError

		if self.register_external_agent_status_raise_error_callback:
			if await self.register_external_agent_status_raise_error_callback():
				raise InterruptedError

		if self.state.stopped:
			raise InterruptedError

		if self.state.paused:
			raise InterruptedError

	@observe(name='agent.step', ignore_output=True, ignore_input=True)
	@time_execution_async('--step')
	async def step(self, step_info: AgentStepInfo | None = None) -> None:
		"""Execute one step of the task"""
		await self.step_executor.execute_step(step_info)


	async def take_step(self, step_info: AgentStepInfo | None = None) -> tuple[bool, bool]:
		"""Take a step

		Returns:
		        Tuple[bool, bool]: (is_done, is_valid)
		"""
		return await self.step_executor.take_step(step_info)

	def _extract_url_from_task(self, task: str) -> str | None:
		"""Extract URL from task string using naive pattern matching."""
		import re

		# URL 抽出の前にメールアドレスを除去する
		task_without_emails = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '', task)

		# 一般的な URL パターンを順に探索
		patterns = [
			r'https?://[^\s<>"\']+',  # http/https を含む完全な URL
			r'(?:www\.)?[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}(?:/[^\s<>"\']*)?',  # サブドメインやパスを含むドメイン名
		]

		found_urls = []
		for pattern in patterns:
			matches = re.finditer(pattern, task_without_emails)
			for match in matches:
				url = match.group(0)

				# URL に含まれない末尾の句読点を削除
				url = re.sub(r'[.,;:!?()\[\]]+$', '', url)
				# スキームが無ければ https:// を付与
				if not url.startswith(('http://', 'https://')):
					url = 'https://' + url
				found_urls.append(url)

		unique_urls = list(set(found_urls))
		# URL が複数見つかった場合は自動オープンを行わない
		if len(unique_urls) > 1:
			self.logger.debug(f'Multiple URLs found ({len(found_urls)}), skipping directly_open_url to avoid ambiguity')
			return None

		# URL が一つだけならそれを返す
		if len(unique_urls) == 1:
			return unique_urls[0]

		return None

	async def run(
		self,
		max_steps: int = 100,
		on_step_start: AgentHookFunc | None = None,
		on_step_end: AgentHookFunc | None = None,
	) -> AgentHistoryList[AgentStructuredOutput]:
		"""Delegate execution to the AgentRunner."""
		return await AgentRunner(self).run(
			max_steps=max_steps,
			on_step_start=on_step_start,
			on_step_end=on_step_end,
		)

	async def multi_act(
		self,
		actions: list[ActionModel],
		check_for_new_elements: bool = True,
	) -> list[ActionResult]:
		"""Execute multiple actions"""
		return await self.step_executor.multi_act(actions, check_for_new_elements=check_for_new_elements)

	async def log_completion(self) -> None:
		"""Log the completion of the task"""
		self.telemetry_handler.log_completion()

	async def rerun_history(
		self,
		history: AgentHistoryList,
		max_retries: int = 3,
		skip_failures: bool = True,
		delay_between_actions: float = 2.0,
	) -> list[ActionResult]:
		return await self.history_manager.rerun_history(
			history,
			max_retries=max_retries,
			skip_failures=skip_failures,
			delay_between_actions=delay_between_actions,
		)

	async def _execute_initial_actions(self) -> None:
		await self.step_executor.execute_initial_actions()


	async def load_and_rerun(self, history_file: str | Path | None = None, **kwargs) -> list[ActionResult]:
		return await self.history_manager.load_and_rerun(history_file, **kwargs)

	def save_history(self, file_path: str | Path | None = None) -> None:
		self.history_manager.save_history(file_path)

	def pause(self) -> None:
		"""Pause the agent before the next step"""
		self.state.paused = True
		self.pause_controller.pause()

	def resume(self) -> None:
		"""Resume the agent"""
		self.state.paused = False
		self.pause_controller.resume()

	def stop(self) -> None:
		"""Stop the agent"""
		self.logger.info('⏹️ Agent stopping')
		self.state.stopped = True
		self.pause_controller.force_resume()

	def _convert_initial_actions(self, actions: list[dict[str, dict[str, Any]]]) -> list[ActionModel]:
		"""Convert dictionary-based actions to ActionModel instances"""
		converted_actions = []
		action_model = self.ActionModel
		for action_dict in actions:
			# 各 action_dict には1つのキーと値が必要
			action_name = next(iter(action_dict))
			params = action_dict[action_name]

			# レジストリからパラメータモデルを取得
			action_info = self.tools.registry.registry.actions[action_name]
			param_model = action_info.param_model

			# パラメータモデルでバリデーションした値を生成
			validated_params = param_model(**params)

			# バリデーション済みパラメータで ActionModel を作成
			action_model = self.ActionModel(**{action_name: validated_params})
			converted_actions.append(action_model)

		return converted_actions

	def _verify_and_setup_llm(self):
		"""
		Verify that the LLM API keys are setup and the LLM API is responding properly.
		Also handles tool calling method detection if in auto mode.
		"""

		# すでに検証済みならスキップ
		if getattr(self.llm, '_verified_api_keys', None) is True or CONFIG.SKIP_LLM_API_KEY_VERIFICATION:
			setattr(self.llm, '_verified_api_keys', True)
			return True

	@property
	def message_manager(self) -> MessageManager:
		return self._message_manager

	async def close(self):
		"""Close all resources"""
		try:
			# keep_alive が無効（または未設定）の場合のみブラウザを終了する
			if self.browser_session is not None:
				if not self.browser_session.browser_profile.keep_alive:
					# BrowserStopEvent を発行し、EventBus をクリアして再生成する
					await self.browser_session.kill()

			# ガーベジコレクションを明示的に実行
			gc.collect()

			# デバッグ用に残っているスレッドと asyncio タスクを表示
			import threading

			threads = threading.enumerate()
			self.logger.debug(f'🧵 Remaining threads ({len(threads)}): {[t.name for t in threads]}')

			# 全ての asyncio タスクを取得
			tasks = asyncio.all_tasks(asyncio.get_event_loop())
			# 現在のコルーチン（close()）を除外
			other_tasks = [t for t in tasks if t != asyncio.current_task()]
			if other_tasks:
				self.logger.debug(f'⚡ Remaining asyncio tasks ({len(other_tasks)}):')
				for task in other_tasks[:10]:  # ログが煩雑にならないよう先頭10件のみ表示
					self.logger.debug(f'  - {task.get_name()}: {task}')
			else:
				self.logger.debug('⚡ No remaining asyncio tasks')

		except Exception as e:
			self.logger.error(f'Error during cleanup: {e}')

		llm = getattr(self, 'llm', None)
		if llm is not None:
			try:
				aclose = getattr(llm, 'aclose', None)
				if callable(aclose):
					result = aclose()
					if inspect.isawaitable(result):
						await result
				close = getattr(llm, 'close', None)
				if callable(close):
					close()
			except Exception as e:
				self.logger.debug(f'Error closing LLM client: {e}')

	async def _update_action_models_for_page(self, page_url: str) -> None:
		"""Update action models with page-specific actions"""
		# 現在のページに応じたフィルタで新しいアクションモデルを作成
		self.ActionModel = self.tools.registry.create_action_model(page_url=page_url)
		# 生成したアクションを反映する出力モデルに差し替え
		if self.settings.flash_mode:
			self.AgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.ActionModel)
		elif self.settings.use_thinking:
			self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)
		else:
			self.AgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.ActionModel)

		# done 用のアクションモデルも更新
		self.DoneActionModel = self.tools.registry.create_action_model(include_actions=['done'], page_url=page_url)
		if self.settings.flash_mode:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.DoneActionModel)
		elif self.settings.use_thinking:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions(self.DoneActionModel)
		else:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.DoneActionModel)

	def get_trace_object(self) -> dict[str, Any]:
		"""Get the trace and trace_details objects for the agent"""

		def extract_task_website(task_text: str) -> str | None:
			url_pattern = r'https?://[^\s<>"\']+|www\.[^\s<>"\']+|[^\s<>"\']+\.[a-z]{2,}(?:/[^\s<>"\']*)?'
			match = re.search(url_pattern, task_text, re.IGNORECASE)
			return match.group(0) if match else None

		def _get_complete_history_without_screenshots(history_data: dict[str, Any]) -> str:
			if 'history' in history_data:
				for item in history_data['history']:
					if 'state' in item and 'screenshot' in item['state']:
						item['state']['screenshot'] = None

			return json.dumps(history_data)

		# 自動生成フィールドを作成
		trace_id = uuid7str()
		timestamp = datetime.now().isoformat()

		# 複数回参照する変数のみ事前に宣言
		structured_output = self.history.structured_output
		structured_output_json = json.dumps(structured_output.model_dump()) if structured_output else None
		final_result = self.history.final_result()
		git_info = get_git_info()
		action_history = self.history.action_history()
		action_errors = self.history.errors()
		urls = self.history.urls()
		usage = self.history.usage

		return {
			'trace': {
				# 自動生成フィールド
				'trace_id': trace_id,
				'timestamp': timestamp,
				'browser_use_version': get_browser_use_version(),
				'git_info': json.dumps(git_info) if git_info else None,
				# Agent の直接的な属性
				'model': self.llm.model,
				'settings': json.dumps(self.settings.model_dump()) if self.settings else None,
				'task_id': self.task_id,
				'task_truncated': self.task[:20000] if len(self.task) > 20000 else self.task,
				'task_website': extract_task_website(self.task),
				# AgentHistoryList 関連の情報
				'structured_output_truncated': (
					structured_output_json[:20000]
					if structured_output_json and len(structured_output_json) > 20000
					else structured_output_json
				),
				'action_history_truncated': json.dumps(action_history) if action_history else None,
				'action_errors': json.dumps(action_errors) if action_errors else None,
				'urls': json.dumps(urls) if urls else None,
				'final_result_response_truncated': (
					final_result[:20000] if final_result and len(final_result) > 20000 else final_result
				),
				'self_report_completed': 1 if self.history.is_done() else 0,
				'self_report_success': 1 if self.history.is_successful() else 0,
				'duration': self.history.total_duration_seconds(),
				'steps_taken': self.history.number_of_steps(),
				'usage': json.dumps(usage.model_dump()) if usage else None,
			},
			'trace_details': {
				# 自動生成フィールド（trace と一致させる）
				'trace_id': trace_id,
				'timestamp': timestamp,
				# Agent の直接的な属性
				'task': self.task,
				# AgentHistoryList 関連の情報
				'structured_output': structured_output_json,
				'final_result_response': final_result,
				'complete_history': _get_complete_history_without_screenshots(
					self.history.model_dump(sensitive_data=self.sensitive_data)
				),
			},
		}

	async def authenticate_cloud_sync(self, show_instructions: bool = True) -> bool:
		"""
		Authenticate with cloud service for future runs.

		This is useful when users want to authenticate after a task has completed
		so that future runs will sync to the cloud.

		Args:
			show_instructions: Whether to show authentication instructions to user

		Returns:
			bool: True if authentication was successful
		"""
		if not hasattr(self, 'cloud_sync') or self.cloud_sync is None:
			self.logger.warning('Cloud sync is not available for this agent')
			return False

		return await self.cloud_sync.authenticate(show_instructions=show_instructions)

	def run_sync(
		self,
		max_steps: int = 100,
		on_step_start: AgentHookFunc | None = None,
		on_step_end: AgentHookFunc | None = None,
	) -> AgentHistoryList[AgentStructuredOutput]:
		"""Synchronous wrapper around the async run method for easier usage without asyncio."""
		import asyncio

		return asyncio.run(self.run(max_steps=max_steps, on_step_start=on_step_start, on_step_end=on_step_end))
