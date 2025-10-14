import asyncio
import gc
import inspect
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar
from urllib.parse import urlparse

from dotenv import load_dotenv

from browser_use.agent.cloud_events import (
	CreateAgentOutputFileEvent,
	CreateAgentSessionEvent,
	CreateAgentStepEvent,
	CreateAgentTaskEvent,
	UpdateAgentTaskEvent,
)
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
from browser_use.agent.filesystem_manager import FilesystemManager
from browser_use.agent.history_manager import HistoryManager
from browser_use.agent.llm_handler import LLMHandler
from browser_use.agent.message_manager.service import MessageManager
from browser_use.agent.prompts import SystemPrompt
from browser_use.agent.step_executor import StepExecutor
from browser_use.agent.telemetry import TelemetryHandler
from browser_use.agent.views import (
	ActionResult,
	AgentHistory,
	AgentHistoryList,
	AgentOutput,
	AgentSettings,
	AgentState,
	AgentStepInfo,
	AgentStructuredOutput,
	BrowserStateHistory,
)
from browser_use.browser.session import DEFAULT_BROWSER_PROFILE
from browser_use.browser.views import BrowserStateSummary
from browser_use.config import CONFIG
from browser_use.filesystem.file_system import FileSystem
from browser_use.observability import observe, observe_debug
from browser_use.sync import CloudSync
from browser_use.telemetry.service import ProductTelemetry
from browser_use.tools.registry.views import ActionModel
from browser_use.tools.service import Tools
from browser_use.utils import (
	_log_pretty_path,
	check_latest_browser_use_version,
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
		task: str,
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
		**kwargs,
	):
		if llm is None:
			default_llm_name = CONFIG.DEFAULT_LLM
			if default_llm_name:
				try:
					from browser_use.llm.models import get_llm_by_name

					llm = get_llm_by_name(default_llm_name)
				except (ImportError, ValueError) as e:
					# ファイル冒頭で用意した logger をそのまま利用する
					logger.warning(
						f'Failed to create default LLM "{default_llm_name}": {e}. Falling back to ChatGoogle(model="gemini-flash-latest")'
					)
					llm = ChatGoogle(model='gemini-flash-latest')
			else:
				# デフォルト LLM が指定されていない場合は元のデフォルトを使う
				llm = ChatGoogle(model='gemini-flash-latest')

		# LLM が ChatBrowserUse の場合は flash_mode を強制的に有効にする
		if llm.provider == 'browser-use':
			flash_mode = True

		if page_extraction_llm is None:
			page_extraction_llm = llm
		if available_file_paths is None:
			available_file_paths = []
		self._initial_available_file_paths = list(available_file_paths)

		# タイムアウトが未設定ならモデル名に応じて決定する
		if llm_timeout is None:

			def _get_model_timeout(llm_model: BaseChatModel) -> int:
				"""Determine timeout based on model name"""
				model_name = getattr(llm_model, 'model', '').lower()
				if 'gemini' in model_name:
					return 45
				elif 'groq' in model_name:
					return 30
				elif 'o3' in model_name or 'claude' in model_name or 'sonnet' in model_name or 'deepseek' in model_name:
					return 90
				else:
					return 60  # 既定のタイムアウト値

			llm_timeout = _get_model_timeout(llm)

		self.id = task_id or uuid7str()
		self.task_id: str = self.id
		self.session_id: str = uuid7str()

		browser_profile = browser_profile or DEFAULT_BROWSER_PROFILE

		# browser と browser_session が同時指定された場合は browser を優先する
		if browser and browser_session:
			raise ValueError('Cannot specify both "browser" and "browser_session" parameters. Use "browser" for the cleaner API.')
		browser_session = browser or browser_session

		self.browser_session = browser_session or BrowserSession(
			browser_profile=browser_profile,
			id=uuid7str()[:-4] + self.id[-4:],  # ログ上で並べて確認しやすいよう末尾4文字を共有
		)

		self.filesystem_manager: FilesystemManager | None = None

		# コアとなる構成要素
		self.task = task
		self.llm = llm
		self.directly_open_url = directly_open_url
		self.include_recent_events = include_recent_events
		self._url_shortening_limit = _url_shortening_limit
		if tools is not None:
			self.tools = tools
		elif controller is not None:
			self.tools = controller
		else:
			# use_vision=False のときはスクリーンショット系アクションを除外
			exclude_actions = ['screenshot'] if use_vision is False else []
			self.tools = Tools(exclude_actions=exclude_actions, display_files_in_done_text=display_files_in_done_text)

		# 構造化出力の設定
		self.output_model_schema = output_model_schema
		if self.output_model_schema is not None:
			self.tools.use_structured_output_action(self.output_model_schema)

		self.sensitive_data = sensitive_data

		self.sample_images = sample_images

		self.settings = AgentSettings(
			use_vision=use_vision,
			vision_detail_level=vision_detail_level,
			save_conversation_path=save_conversation_path,
			save_conversation_path_encoding=save_conversation_path_encoding,
			max_failures=max_failures,
			override_system_message=override_system_message,
			extend_system_message=extend_system_message,
			generate_gif=generate_gif,
			include_attributes=include_attributes,
			max_actions_per_step=max_actions_per_step,
			use_thinking=use_thinking,
			flash_mode=flash_mode,
			max_history_items=max_history_items,
			page_extraction_llm=page_extraction_llm,
			calculate_cost=calculate_cost,
			include_tool_call_examples=include_tool_call_examples,
			llm_timeout=llm_timeout,
			step_timeout=step_timeout,
			final_response_after_failure=final_response_after_failure,
		)

		# トークンコスト算出サービス
		self.token_cost_service = TokenCost(include_cost=calculate_cost)
		self.token_cost_service.register_llm(llm)
		self.token_cost_service.register_llm(page_extraction_llm)

		# 状態の初期化
		self.state = injected_agent_state or AgentState()

		# 履歴の初期化
		self.history = AgentHistoryList(history=[], usage=None)
		self.history_manager = HistoryManager(self)

		# エージェント用ディレクトリの初期化
		import time

		timestamp = int(time.time())
		base_tmp = Path(tempfile.gettempdir())
		self.agent_directory = base_tmp / f'browser_use_agent_{self.id}_{timestamp}'

		# ファイルシステムとスクリーンショットサービスを準備
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

		# アクションの初期セットアップ
		self._setup_action_models()
		self._set_browser_use_version_and_source(source)

		initial_url = None

		# 初期アクションが無い場合に限り URL 自動ロードを行う
		if self.directly_open_url and not self.state.follow_up_task and not initial_actions:
			initial_url = self._extract_url_from_task(self.task)
			if initial_url:
				self.logger.info(f'🔗 Found URL in task: {initial_url}, adding as initial action...')
				initial_actions = [{'navigate': {'url': initial_url, 'new_tab': False}}]

		self.initial_url = initial_url

		self.initial_actions = self._convert_initial_actions(initial_actions) if initial_actions else None
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
			task=task,
			system_message=SystemPrompt(
				max_actions_per_step=self.settings.max_actions_per_step,
				override_system_message=override_system_message,
				extend_system_message=extend_system_message,
				use_thinking=self.settings.use_thinking,
				flash_mode=self.settings.flash_mode,
			).get_system_message(),
			file_system=self.file_system,
			state=self.state.message_manager_state,
			use_thinking=self.settings.use_thinking,
			# 以前 MessageManagerSettings にあったパラメータ
			include_attributes=self.settings.include_attributes,
			sensitive_data=sensitive_data,
			max_history_items=self.settings.max_history_items,
			vision_detail_level=self.settings.vision_detail_level,
			include_tool_call_examples=self.settings.include_tool_call_examples,
			include_recent_events=self.include_recent_events,
			sample_images=self.sample_images,
		)

		if self.sensitive_data:
			# domain ごとの資格情報が含まれているか確認する
			has_domain_specific_credentials = any(isinstance(v, dict) for v in self.sensitive_data.values())

			# allowed_domains が設定されていない場合はセキュリティ警告を出す
			if not self.browser_profile.allowed_domains:
				self.logger.error(
					'⚠️ Agent(sensitive_data=••••••••) was provided but Browser(allowed_domains=[...]) is not locked down! ⚠️\n'
					'          ☠️ If the agent visits a malicious website and encounters a prompt-injection attack, your sensitive_data may be exposed!\n\n'
					'   \n'
				)

			# ドメイン単位の資格情報を扱う場合はパターンを検証する
			elif has_domain_specific_credentials:
				# ドメインパターンが allowed_domains に含まれているか確認する
				domain_patterns = [k for k, v in self.sensitive_data.items() if isinstance(v, dict)]

				# 各ドメインパターンを allowed_domains と照合する
				for domain_pattern in domain_patterns:
					is_allowed = False
					for allowed_domain in self.browser_profile.allowed_domains:
						# URL マッチングが不要な特例
						if domain_pattern == allowed_domain or allowed_domain == '*':
							is_allowed = True
							break

						# 比較のためスキームを除いたドメイン部に変換する
						pattern_domain = domain_pattern.split('://')[-1] if '://' in domain_pattern else domain_pattern
						allowed_domain_part = allowed_domain.split('://')[-1] if '://' in allowed_domain else allowed_domain

						# 許可されたドメインによりパターンが包含されているか確認する
						# 例: "google.com" は "*.google.com" に含まれる
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

		# コールバック
		self.register_new_step_callback = register_new_step_callback
		self.register_done_callback = register_done_callback
		self.register_should_stop_callback = register_should_stop_callback
		self.register_external_agent_status_raise_error_callback = register_external_agent_status_raise_error_callback

		# テレメトリ
		self.telemetry = ProductTelemetry()
		self.telemetry_handler = TelemetryHandler(self)
		self.llm_handler = LLMHandler(self)
		self.step_executor = StepExecutor(self)

		# WAL 永続化付きのイベントバス
		# 既定パスは ~/.config/browseruse/events/{agent_session_id}.jsonl
		# wal_path = CONFIG.BROWSER_USE_CONFIG_DIR / 'events' / f'{self.session_id}.jsonl'
		self.eventbus = EventBus(name=f'Agent_{str(self.id)[-4:]}')

		# クラウド同期サービス
		self.enable_cloud_sync = CONFIG.BROWSER_USE_CLOUD_SYNC
		if self.enable_cloud_sync or cloud_sync is not None:
			self.cloud_sync = cloud_sync or CloudSync()
			# クラウド同期ハンドラを登録
			self.eventbus.on('*', self.cloud_sync.handle_event)
		else:
			self.cloud_sync = None

		if self.settings.save_conversation_path:
			self.settings.save_conversation_path = Path(self.settings.save_conversation_path).expanduser().resolve()
			self.logger.info(f'💬 Saving conversation to {_log_pretty_path(self.settings.save_conversation_path)}')

		# イベント駆動の一時停止制御（シリアライズの都合で AgentState には含めない）
		self._external_pause_event = asyncio.Event()
		self._external_pause_event.set()

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

	@observe(name='agent.run', metadata={'task': '{{task}}', 'debug': '{{debug}}'})
	@time_execution_async('--run')
	async def run(
		self,
		max_steps: int = 100,
		on_step_start: AgentHookFunc | None = None,
		on_step_end: AgentHookFunc | None = None,
	) -> AgentHistoryList[AgentStructuredOutput]:
		"""Execute the task with maximum number of steps"""

		loop = asyncio.get_event_loop()
		agent_run_error: str | None = None  # エラーメッセージ格納用の初期値
		self._force_exit_telemetry_logged = False  # 強制終了テレメトリを記録済みかを示すフラグ

		# このエージェント専用のシグナルハンドラを設定
		from browser_use.utils import SignalHandler

		# 2回目の CTRL+C で呼ばれるカスタム終了コールバック
		def on_force_exit_log_telemetry():
			self.telemetry_handler.log_agent_event(max_steps=max_steps, agent_run_error='SIGINT: Cancelled by user')
			# テレメトリインスタンスを明示的に flush
			if hasattr(self, 'telemetry') and self.telemetry:
				self.telemetry.flush()
			self._force_exit_telemetry_logged = True  # フラグを立てる

		signal_handler = SignalHandler(
			loop=loop,
			pause_callback=self.pause,
			resume_callback=self.resume,
			custom_exit_callback=on_force_exit_log_telemetry,  # 新しいテレメトリコールバックを登録
			exit_on_second_int=True,
		)
		signal_handler.register()

		try:
			await self.telemetry_handler.log_agent_run()

			self.logger.debug(
				f'🔧 Agent setup: Agent Session ID {self.session_id[-4:]}, Task ID {self.task_id[-4:]}, Browser Session ID {self.browser_session.id[-4:] if self.browser_session else "None"} {"(connecting via CDP)" if (self.browser_session and self.browser_session.cdp_url) else "(launching local browser)"}'
			)

			# Initialize timing for session and task
			self._session_start_time = time.time()
			self._task_start_time = self._session_start_time  # タスク開始時刻も初期化

			# 初回実行時のみセッションイベントを送信
			if not self.state.session_initialized:
				if self.enable_cloud_sync:
					self.logger.debug('📡 Dispatching CreateAgentSessionEvent...')
					# run() 開始時に CreateAgentSessionEvent を発火
					self.eventbus.dispatch(CreateAgentSessionEvent.from_agent(self))

					# バックエンドでセッションが作成されるまで短時間待機
					await asyncio.sleep(0.2)

				self.state.session_initialized = True

			if self.enable_cloud_sync:
				self.logger.debug('📡 Dispatching CreateAgentTaskEvent...')
				# run() 開始時に CreateAgentTaskEvent を発火
				self.eventbus.dispatch(CreateAgentTaskEvent.from_agent(self))

			# まだステップを踏んでいない場合のみ起動メッセージを表示
			self.telemetry_handler.log_first_step_startup()
			# ブラウザセッションを開始しウォッチドッグを取り付ける
			await self.browser_session.start()

			# 本来 try-catch は不要だがコールバックが InterruptedError を送る可能性がある
			try:
				await self.step_executor.execute_initial_actions()
			except InterruptedError:
				pass
			except Exception as e:
				raise e

			self.logger.debug(f'🔄 Starting main execution loop with max {max_steps} steps...')
			for step in range(max_steps):
				# 一元化された一時停止管理を利用
				if self.state.paused:
					self.logger.debug(f'⏸️ Step {step}: Agent paused, waiting to resume...')
					await self._external_pause_event.wait()
					signal_handler.reset()

				# 失敗が多すぎる場合に停止すべきか判定（final_response_after_failure が True なら最後にもう一度試みる）
				if (self.state.consecutive_failures) >= self.settings.max_failures + int(
					self.settings.final_response_after_failure
				):
					self.logger.error(f'❌ Stopping due to {self.settings.max_failures} consecutive failures')
					agent_run_error = f'Stopped due to {self.settings.max_failures} consecutive failures'
					break

				# 各ステップ前に停止フラグを確認
				if self.state.stopped:
					self.logger.info('🛑 Agent stopped')
					agent_run_error = 'Agent stopped programmatically'
					break

				if on_step_start is not None:
					await on_step_start(self)

				self.logger.debug(f'🚶 Starting step {step + 1}/{max_steps}...')
				step_info = AgentStepInfo(step_number=step, max_steps=max_steps)

				try:
					await asyncio.wait_for(
						self.step(step_info),
						timeout=self.settings.step_timeout,
					)
					self.logger.debug(f'✅ Completed step {step + 1}/{max_steps}')
				except TimeoutError:
					# ステップのタイムアウトを丁寧に処理
					error_msg = f'Step {step + 1} timed out after {self.settings.step_timeout} seconds'
					self.logger.error(f'⏰ {error_msg}')
					self.state.consecutive_failures += 1
					self.state.last_result = [ActionResult(error=error_msg)]

				if on_step_end is not None:
					await on_step_end(self)

				if self.history.is_done():
					self.logger.debug(f'🎯 Task completed after {step + 1} steps!')
					self.telemetry_handler.log_completion()

					if self.register_done_callback:
						if inspect.iscoroutinefunction(self.register_done_callback):
							await self.register_done_callback(self.history)
						else:
							self.register_done_callback(self.history)

					# タスク完了
					break
			else:
				agent_run_error = 'Failed to complete task in maximum steps'

				self.history.add_item(
					AgentHistory(
						model_output=None,
						result=[ActionResult(error=agent_run_error, include_in_memory=True)],
						state=BrowserStateHistory(
							url='',
							title='',
							tabs=[],
							interacted_element=[],
							screenshot_path=None,
						),
						metadata=None,
					)
				)

				self.logger.info(f'❌ {agent_run_error}')

			self.logger.debug('📊 Collecting usage summary...')
			self.history.usage = await self.token_cost_service.get_usage_summary()

			# モデル出力スキーマが未設定ならここで反映
			if self.history._output_model_schema is None and self.output_model_schema is not None:
				self.history._output_model_schema = self.output_model_schema

			self.logger.debug('🏁 Agent.run() completed successfully')
			return self.history

		except KeyboardInterrupt:
			# シグナルハンドラで処理済みだが直接の KeyboardInterrupt も受ける
			self.logger.debug('Got KeyboardInterrupt during execution, returning current history')
			agent_run_error = 'KeyboardInterrupt'

			self.history.usage = await self.token_cost_service.get_usage_summary()

			return self.history

		except Exception as e:
			self.logger.error(f'Agent run failed with exception: {e}', exc_info=True)
			agent_run_error = str(e)
			raise e

		finally:
			# トークン使用量のサマリを記録
			await self.token_cost_service.log_usage_summary()

			# 後片付けの前にシグナルハンドラを解除
			signal_handler.unregister()

			if not self._force_exit_telemetry_logged:  # 変更点: フラグを確認して未送信なら記録
				try:
					self.telemetry_handler.log_agent_event(max_steps=max_steps, agent_run_error=agent_run_error)
				except Exception as log_e:  # テレメトリ記録自体の失敗を捕捉
					self.logger.error(f'Failed to log telemetry event: {log_e}', exc_info=True)
			else:
				# 既に SIGINT 用テレメトリを送信済みであることを通知
				self.logger.debug('Telemetry for force exit (SIGINT) was logged by custom exit callback.')

			# 備考: CreateAgentSessionEvent と CreateAgentTaskEvent は run() 開始時に送信し、
			#      生成時点で CREATE イベントが届くようにしている

			# run() 終了時に最終状態を UpdateAgentTaskEvent で送信
			if self.enable_cloud_sync:
				self.eventbus.dispatch(UpdateAgentTaskEvent.from_agent(self))

			# イベントバス停止前に必要であれば GIF を生成
			if self.settings.generate_gif:
				output_path: str = 'agent_history.gif'
				if isinstance(self.settings.generate_gif, str):
					output_path = self.settings.generate_gif

				# 起動コストを抑えるための遅延インポート
				from browser_use.agent.gif import create_history_gif

				create_history_gif(task=self.task, history=self.history, output_path=output_path)

				# 実際に GIF が生成された場合のみイベントを発行
				if Path(output_path).exists():
					output_event = await CreateAgentOutputFileEvent.from_agent_and_file(self, output_path)
					self.eventbus.dispatch(output_event)

			# クラウド認証の開始を少し待ちつつ URL 表示を促す（完了までは待たない）
			if self.enable_cloud_sync and hasattr(self, 'cloud_sync') and self.cloud_sync is not None:
				if self.cloud_sync.auth_task and not self.cloud_sync.auth_task.done():
					try:
						# 最大1秒だけ待機して認証URLが出るのを待つ
						await asyncio.wait_for(self.cloud_sync.auth_task, timeout=1.0)
					except TimeoutError:
						logger.debug('Cloud authentication started - continuing in background')
					except Exception as e:
						logger.debug(f'Cloud authentication error: {e}')

			# イベントバスを優雅に停止（全イベント処理を待つ）
			# 複数エージェントのテストでデッドロックしないよう余裕を持ったタイムアウトを用いる
			await self.eventbus.stop(timeout=3.0)

			await self.close()

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

	# --- Backwards compatibility wrappers for URL processing helpers ---

	def _process_messsages_and_replace_long_urls_shorter_ones(self, input_messages: list['BaseMessage']) -> dict[str, str]:
		"""Backward-compatible proxy to LLMHandler URL-shortening helper."""
		return self.llm_handler._process_messages_and_shorten_urls(input_messages)

	def _recursive_process_all_strings_inside_pydantic_model(self, model: 'BaseModel', url_replacements: dict[str, str]) -> None:
		"""Backward-compatible proxy to restore URLs inside Pydantic models."""
		self.llm_handler._recursive_process_model(model, url_replacements)

	def _replace_urls_in_text(self, text: str) -> tuple[str, dict[str, str]]:
		"""Backward-compatible proxy to shorten URLs within plain text."""
		return self.llm_handler._replace_urls_in_text(text)

	def _replace_shortened_urls_in_string(self, text: str, url_replacements: dict[str, str]) -> str:
		"""Backward-compatible proxy to restore original URLs inside a string."""
		return self.llm_handler._replace_shortened_urls_in_string(text, url_replacements)

	def _recursive_process_dict(self, dictionary: dict, url_replacements: dict[str, str]) -> None:
		"""Backward-compatible proxy to process dictionaries for URL restoration."""
		self.llm_handler._recursive_process_dict(dictionary, url_replacements)

	def _recursive_process_list_or_tuple(self, container: list | tuple, url_replacements: dict[str, str]) -> list | tuple:
		"""Backward-compatible proxy to process iterables for URL restoration."""
		return self.llm_handler._recursive_process_iterable(container, url_replacements)

	def pause(self) -> None:
		"""Pause the agent before the next step"""
		print('\n\n⏸️ Paused the agent and left the browser open.\n\tPress [Enter] to resume or [Ctrl+C] again to quit.')
		self.state.paused = True
		self._external_pause_event.clear()

	def resume(self) -> None:
		"""Resume the agent"""
		# TODO: ローカル環境ではブラウザが閉じてしまう課題あり
		print('----------------------------------------------------------------------')
		print('▶️  Resuming agent execution where it left off...\n')
		self.state.paused = False
		self._external_pause_event.set()

	def stop(self) -> None:
		"""Stop the agent"""
		self.logger.info('⏹️ Agent stopping')
		self.state.stopped = True

		# 一時停止イベントを解放し、待機中の処理に停止状態を知らせる
		self._external_pause_event.set()

		# タスク停止フラグ

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
