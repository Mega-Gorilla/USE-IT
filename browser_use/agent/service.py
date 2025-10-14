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
from urllib.parse import urlparse

from dotenv import load_dotenv

from browser_use.agent.cloud_events import (
	CreateAgentOutputFileEvent,
	CreateAgentSessionEvent,
	CreateAgentStepEvent,
	CreateAgentTaskEvent,
	UpdateAgentTaskEvent,
)
from browser_use.agent.message_manager.utils import save_conversation
from browser_use.llm.base import BaseChatModel
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.messages import BaseMessage, ContentPartImageParam, ContentPartTextParam, UserMessage
from browser_use.tokens.service import TokenCost

load_dotenv()

from bubus import EventBus
from pydantic import BaseModel, ValidationError
from uuid_extensions import uuid7str

from browser_use import Browser, BrowserProfile, BrowserSession

# 起動時に重い agent.views の読み込みを避けるため GIF のインポートは遅延させる
# （参考） browser_use.agent.gif から create_history_gif をインポートするサンプル  # 遅延インポート例
from browser_use.agent.filesystem_manager import FilesystemManager
from browser_use.agent.message_manager.service import (
	MessageManager,
)
from browser_use.agent.prompts import SystemPrompt
from browser_use.agent.views import (
	ActionResult,
	AgentError,
	AgentHistory,
	AgentHistoryList,
	AgentOutput,
	AgentSettings,
	AgentState,
	AgentStepInfo,
	AgentStructuredOutput,
	BrowserStateHistory,
	StepMetadata,
)
from browser_use.browser.session import DEFAULT_BROWSER_PROFILE
from browser_use.browser.views import BrowserStateSummary
from browser_use.config import CONFIG
from browser_use.dom.views import DOMInteractedElement
from browser_use.filesystem.file_system import FileSystem
from browser_use.observability import observe, observe_debug
from browser_use.sync import CloudSync
from browser_use.telemetry.service import ProductTelemetry
from browser_use.telemetry.views import AgentTelemetryEvent
from browser_use.tools.registry.views import ActionModel
from browser_use.tools.service import Tools
from browser_use.utils import (
	URL_PATTERN,
	_log_pretty_path,
	check_latest_browser_use_version,
	get_browser_use_version,
	get_git_info,
	time_execution_async,
	time_execution_sync,
)

logger = logging.getLogger(__name__)


def log_response(response: AgentOutput, registry=None, logger=None) -> None:
	"""Utility function to log the model's response."""

	# logger が渡されなかった場合はモジュールの logger を使う
	if logger is None:
		logger = logging.getLogger(__name__)

	# Thinking が存在する場合のみ出力する
	if response.current_state.thinking:
		logger.debug(f'💡 Thinking:\n{response.current_state.thinking}')

	# 評価テキストがあれば記録する
	eval_goal = response.current_state.evaluation_previous_goal
	if eval_goal:
		if 'success' in eval_goal.lower():
			emoji = '👍'
			# 成功時は緑色で表示
			logger.info(f'  \033[32m{emoji} Eval: {eval_goal}\033[0m')
		elif 'failure' in eval_goal.lower():
			emoji = '⚠️'
			# 失敗時は赤色で表示
			logger.info(f'  \033[31m{emoji} Eval: {eval_goal}\033[0m')
		else:
			emoji = '❔'
			# 不明／中立は色を付けない
			logger.info(f'  {emoji} Eval: {eval_goal}')

	# メモリがあれば必ず出力する
	if response.current_state.memory:
		logger.debug(f'🧠 Memory: {response.current_state.memory}')

	# 次の目標がある場合のみ出力する
	next_goal = response.current_state.next_goal
	if next_goal:
		# 次の目標は青色で表示
		logger.info(f'  \033[34m🎯 Next goal: {next_goal}\033[0m')
	else:
		logger.info('')  # 見やすさのため空行を挿入


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
		# 例外が発生する前に計測を開始しておく

		self.step_start_time = time.time()

		browser_state_summary = None

		try:
			# フェーズ1: コンテキストとタイミングの準備
			browser_state_summary = await self._prepare_context(step_info)

			# フェーズ2: モデル推論とアクション実行
			await self._get_next_action(browser_state_summary)
			await self._execute_actions()

			# フェーズ3: 後処理
			await self._post_process()

		except Exception as e:
			# すべての例外をここで受け止める
			await self._handle_step_error(e)

		finally:
			await self._finalize(browser_state_summary)

	async def _prepare_context(self, step_info: AgentStepInfo | None = None) -> BrowserStateSummary:
		"""Prepare the context for the step: browser state, action models, page actions"""
		# step_start_time は step() 側で設定済み

		assert self.browser_session is not None, 'BrowserSession is not set up'

		self.logger.debug(f'🌐 Step {self.state.n_steps}: Getting browser state...')
		# すべてのステップでスクリーンショットを取得する
		self.logger.debug('📸 Requesting browser state with include_screenshot=True')
		browser_state_summary = await self.browser_session.get_browser_state_summary(
			include_screenshot=True,  # use_vision=False でもクラウド同期のため常に撮影（高速化済み）
			include_recent_events=self.include_recent_events,
		)
		if browser_state_summary.screenshot:
			self.logger.debug(f'📸 Got browser state WITH screenshot, length: {len(browser_state_summary.screenshot)}')
		else:
			self.logger.debug('📸 Got browser state WITHOUT screenshot')

		# ブラウザ状態取得後にダウンロードの変化を確認（PDF 自動ダウンロード等に対応）
		await self._check_and_update_downloads(f'Step {self.state.n_steps}: after getting browser state')

		self._log_step_context(browser_state_summary)
		await self._check_stop_or_pause()

		# ページ固有のアクションでモデルを更新
		self.logger.debug(f'📝 Step {self.state.n_steps}: Updating action models...')
		await self._update_action_models_for_page(browser_state_summary.url)

		# ページ専用のアクション記述を取得
		page_filtered_actions = self.tools.registry.get_prompt_description(browser_state_summary.url)

		# ページ専用アクションは browser_state のメッセージに直接含める
		self.logger.debug(f'💬 Step {self.state.n_steps}: Creating state messages for context...')

		self._message_manager.create_state_messages(
			browser_state_summary=browser_state_summary,
			model_output=self.state.last_model_output,
			result=self.state.last_result,
			step_info=step_info,
			use_vision=self.settings.use_vision,
			page_filtered_actions=page_filtered_actions if page_filtered_actions else None,
			sensitive_data=self.sensitive_data,
			available_file_paths=self.available_file_paths,  # 常に最新の available_file_paths を渡す
		)

		await self._force_done_after_last_step(step_info)
		await self._force_done_after_failure()
		return browser_state_summary

	@observe_debug(ignore_input=True, name='get_next_action')
	async def _get_next_action(self, browser_state_summary: BrowserStateSummary) -> None:
		"""Execute LLM interaction with retry logic and handle callbacks"""
		input_messages = self._message_manager.get_messages()
		self.logger.debug(
			f'🤖 Step {self.state.n_steps}: Calling LLM with {len(input_messages)} messages (model: {self.llm.model})...'
		)

		try:
			model_output = await asyncio.wait_for(
				self._get_model_output_with_retry(input_messages), timeout=self.settings.llm_timeout
			)
		except TimeoutError:

			@observe(name='_llm_call_timed_out_with_input')
			async def _log_model_input_to_lmnr(input_messages: list[BaseMessage]) -> None:
				"""Log the model input"""
				pass

			await _log_model_input_to_lmnr(input_messages)

			raise TimeoutError(
				f'LLM call timed out after {self.settings.llm_timeout} seconds. Keep your thinking and output short.'
			)

		self.state.last_model_output = model_output

		# モデル応答取得後にも一時停止／停止要求を再確認
		await self._check_stop_or_pause()

		# コールバック処理と会話ログ保存を実行
		await self._handle_post_llm_processing(browser_state_summary, input_messages)

		# 履歴に確定する前に Ctrl+C などで中断されていないか再チェック
		await self._check_stop_or_pause()

	async def _execute_actions(self) -> None:
		"""Execute the actions from model output"""
		if self.state.last_model_output is None:
			raise ValueError('No model output to execute actions from')

		self.logger.debug(f'⚡ Step {self.state.n_steps}: Executing {len(self.state.last_model_output.action)} actions...')
		result = await self.multi_act(self.state.last_model_output.action)
		self.logger.debug(f'✅ Step {self.state.n_steps}: Actions completed')

		self.state.last_result = result

	async def _post_process(self) -> None:
		"""Handle post-action processing like download tracking and result logging"""
		assert self.browser_session is not None, 'BrowserSession is not set up'

		# アクション実行後に新しいダウンロードがないか確認
		await self._check_and_update_downloads('after executing actions')

		# アクションエラーが単一件だけかどうか確認
		if self.state.last_result and len(self.state.last_result) == 1 and self.state.last_result[-1].error:
			self.state.consecutive_failures += 1
			self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures: {self.state.consecutive_failures}')
			return

		self.state.consecutive_failures = 0
		self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures reset to: {self.state.consecutive_failures}')

		# 完了結果をログに出力
		if self.state.last_result and len(self.state.last_result) > 0 and self.state.last_result[-1].is_done:
			success = self.state.last_result[-1].success
			if success:
				# 成功時は緑色で表示
				self.logger.info(f'\n📄 \033[32m Final Result:\033[0m \n{self.state.last_result[-1].extracted_content}\n\n')
			else:
				# 失敗時は赤色で表示
				self.logger.info(f'\n📄 \033[31m Final Result:\033[0m \n{self.state.last_result[-1].extracted_content}\n\n')
			if self.state.last_result[-1].attachments:
				total_attachments = len(self.state.last_result[-1].attachments)
				for i, file_path in enumerate(self.state.last_result[-1].attachments):
					self.logger.info(f'👉 Attachment {i + 1 if total_attachments > 1 else ""}: {file_path}')

	async def _handle_step_error(self, error: Exception) -> None:
		"""Handle all types of errors that can occur during a step"""

		# InterruptedError は特別扱い
		if isinstance(error, InterruptedError):
			error_msg = 'The agent was interrupted mid-step' + (f' - {str(error)}' if str(error) else '')
			self.logger.error(f'{error_msg}')
			return

		# それ以外の例外全般を処理
		include_trace = self.logger.isEnabledFor(logging.DEBUG)
		error_msg = AgentError.format_error(error, include_trace=include_trace)
		prefix = f'❌ Result failed {self.state.consecutive_failures + 1}/{self.settings.max_failures + int(self.settings.final_response_after_failure)} times:\n '
		self.state.consecutive_failures += 1

		if 'Could not parse response' in error_msg or 'tool_use_failed' in error_msg:
			# モデルに期待される出力形式を再認識させるためのヒントを与える
			logger.error(f'Model: {self.llm.model} failed')
			logger.error(f'{prefix}{error_msg}')
		else:
			self.logger.error(f'{prefix}{error_msg}')

		self.state.last_result = [ActionResult(error=error_msg)]
		return None

	async def _finalize(self, browser_state_summary: BrowserStateSummary | None) -> None:
		"""Finalize the step with history, logging, and events"""
		step_end_time = time.time()
		if not self.state.last_result:
			return

		if browser_state_summary:
			metadata = StepMetadata(
				step_number=self.state.n_steps,
				step_start_time=self.step_start_time,
				step_end_time=step_end_time,
			)

			# 本家ブランチと同じく _make_history_item を利用
			await self._make_history_item(
				self.state.last_model_output,
				browser_state_summary,
				self.state.last_result,
				metadata,
				state_message=self._message_manager.last_state_message_text,
			)

		# ステップ完了の概要をログ出力
		self._log_step_completion_summary(self.step_start_time, self.state.last_result)

		# ステップ完了後にファイルシステム状態を保存
		self.save_file_system_state()

		# ステップ生成・実行の両イベントを発行
		if browser_state_summary and self.state.last_model_output:
			# イベント用にステップの要点を抽出
			actions_data = []
			if self.state.last_model_output.action:
				for action in self.state.last_model_output.action:
					action_dict = action.model_dump() if hasattr(action, 'model_dump') else {}
					actions_data.append(action_dict)

			# クラウド同期が有効な場合のみ CreateAgentStepEvent を送信
			if self.enable_cloud_sync:
				step_event = CreateAgentStepEvent.from_agent_step(
					self,
					self.state.last_model_output,
					self.state.last_result,
					actions_data,
					browser_state_summary,
				)
				self.eventbus.dispatch(step_event)

		# ステップ完了後にカウンタを進める
		self.state.n_steps += 1

	async def _force_done_after_last_step(self, step_info: AgentStepInfo | None = None) -> None:
		"""Handle special processing for the last step"""
		if step_info and step_info.is_last_step():
			# 最終ステップであることを明示するメッセージを追加
			msg = 'Now comes your last step. Use only the "done" action now. No other actions - so here your action sequence must have length 1.'
			msg += '\nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed.'
			msg += '\nIf the task is fully finished, set success in "done" to true.'
			msg += '\nInclude everything you found out for the ultimate task in the done text.'
			self.logger.debug('Last step finishing up')
			self._message_manager._add_context_message(UserMessage(content=msg))
			self.AgentOutput = self.DoneAgentOutput

	async def _force_done_after_failure(self) -> None:
		"""Force done after failure"""
		# リカバリ用メッセージを生成
		if self.state.consecutive_failures >= self.settings.max_failures and self.settings.final_response_after_failure:
			msg = f'You have failed {self.settings.max_failures} consecutive times. This is your final step to complete the task or provide what you found. '
			msg += 'Use only the "done" action now. No other actions - so here your action sequence must have length 1.'
			msg += '\nIf the task could not be completed due to the failures, set success in "done" to false!'
			msg += '\nInclude everything you found out for the task in the done text.'

			self.logger.debug('Force done action, because we reached max_failures.')
			self._message_manager._add_context_message(UserMessage(content=msg))
			self.AgentOutput = self.DoneAgentOutput

	async def _get_model_output_with_retry(self, input_messages: list[BaseMessage]) -> AgentOutput:
		"""Get model output with retry logic for empty actions"""
		model_output = await self.get_model_output(input_messages)
		self.logger.debug(
			f'✅ Step {self.state.n_steps}: Got LLM response with {len(model_output.action) if model_output.action else 0} actions'
		)

		if (
			not model_output.action
			or not isinstance(model_output.action, list)
			or all(action.model_dump() == {} for action in model_output.action)
		):
			self.logger.warning('Model returned empty action. Retrying...')

			clarification_message = UserMessage(
				content='You forgot to return an action. Please respond with a valid JSON action according to the expected schema with your assessment and next actions.'
			)

			retry_messages = input_messages + [clarification_message]
			model_output = await self.get_model_output(retry_messages)

			if not model_output.action or all(action.model_dump() == {} for action in model_output.action):
				self.logger.warning('Model still returned empty after retry. Inserting safe noop action.')
				action_instance = self.ActionModel()
				setattr(
					action_instance,
					'done',
					{
						'success': False,
						'text': 'No next action returned by LLM!',
					},
				)
				model_output.action = [action_instance]

		return model_output

	async def _handle_post_llm_processing(
		self,
		browser_state_summary: BrowserStateSummary,
		input_messages: list[BaseMessage],
	) -> None:
		"""Handle callbacks and conversation saving after LLM interaction"""
		if self.register_new_step_callback and self.state.last_model_output:
			if inspect.iscoroutinefunction(self.register_new_step_callback):
				await self.register_new_step_callback(
					browser_state_summary,
					self.state.last_model_output,
					self.state.n_steps,
				)
			else:
				self.register_new_step_callback(
					browser_state_summary,
					self.state.last_model_output,
					self.state.n_steps,
				)

		if self.settings.save_conversation_path and self.state.last_model_output:
			# save_conversation_path はディレクトリとして扱う（他の記録パスと揃える）
			conversation_dir = Path(self.settings.save_conversation_path)
			conversation_filename = f'conversation_{self.id}_{self.state.n_steps}.txt'
			target = conversation_dir / conversation_filename
			await save_conversation(
				input_messages,
				self.state.last_model_output,
				target,
				self.settings.save_conversation_path_encoding,
			)

	async def _make_history_item(
		self,
		model_output: AgentOutput | None,
		browser_state_summary: BrowserStateSummary,
		result: list[ActionResult],
		metadata: StepMetadata | None = None,
		state_message: str | None = None,
	) -> None:
		"""Create and store history item"""

		if model_output:
			interacted_elements = AgentHistory.get_interacted_element(model_output, browser_state_summary.dom_state.selector_map)
		else:
			interacted_elements = [None]

		# スクリーンショットを保存してパスを取得
		screenshot_path = None
		if browser_state_summary.screenshot:
			self.logger.debug(
				f'📸 Storing screenshot for step {self.state.n_steps}, screenshot length: {len(browser_state_summary.screenshot)}'
			)
			screenshot_path = await self.screenshot_service.store_screenshot(browser_state_summary.screenshot, self.state.n_steps)
			self.logger.debug(f'📸 Screenshot stored at: {screenshot_path}')
		else:
			self.logger.debug(f'📸 No screenshot in browser_state_summary for step {self.state.n_steps}')

		state_history = BrowserStateHistory(
			url=browser_state_summary.url,
			title=browser_state_summary.title,
			tabs=browser_state_summary.tabs,
			interacted_element=interacted_elements,
			screenshot_path=screenshot_path,
		)

		history_item = AgentHistory(
			model_output=model_output,
			result=result,
			state=state_history,
			metadata=metadata,
			state_message=state_message,
		)

		self.history.add_item(history_item)

	def _remove_think_tags(self, text: str) -> str:
		THINK_TAGS = re.compile(r'<think>.*?</think>', re.DOTALL)
		STRAY_CLOSE_TAG = re.compile(r'.*?</think>', re.DOTALL)
		# 手順1: 正しく閉じられた <think>...</think> を削除する
		text = re.sub(THINK_TAGS, '', text)
		# 手順2: 閉じタグ </think> のみ残っている場合はそこまでの文字列をすべて削除する
		text = re.sub(STRAY_CLOSE_TAG, '', text)
		return text.strip()

	# region - URL 置換処理
	def _replace_urls_in_text(self, text: str) -> tuple[str, dict[str, str]]:
		"""Replace URLs in a text string"""

		replaced_urls: dict[str, str] = {}

		def replace_url(match: re.Match) -> str:
			"""Url can only have 1 query and 1 fragment"""
			import hashlib

			original_url = match.group(0)

			# クエリやフラグメントが始まる位置を特定
			query_start = original_url.find('?')
			fragment_start = original_url.find('#')

			# クエリまたはフラグメントのうち最も早い位置を取得
			after_path_start = len(original_url)  # 既定値: クエリ／フラグメントなし
			if query_start != -1:
				after_path_start = min(after_path_start, query_start)
			if fragment_start != -1:
				after_path_start = min(after_path_start, fragment_start)

			# URL をパスまでの基部とクエリ＋フラグメントに分割
			base_url = original_url[:after_path_start]
			after_path = original_url[after_path_start:]

			# after_path が制限以内なら短縮しない
			if len(after_path) <= self._url_shortening_limit:
				return original_url

			# after_path が長過ぎる場合は切り詰めてハッシュを付与
			if after_path:
				truncated_after_path = after_path[: self._url_shortening_limit]
				# after_path 全体から短いハッシュを作成
				hash_obj = hashlib.md5(after_path.encode('utf-8'))
				short_hash = hash_obj.hexdigest()[:7]
				# 短縮 URL を生成
				shortened = f'{base_url}{truncated_after_path}...{short_hash}'
				# 元の URL より短くなる場合のみ採用
				if len(shortened) < len(original_url):
					replaced_urls[shortened] = original_url
					return shortened

			return original_url

		return URL_PATTERN.sub(replace_url, text), replaced_urls

	def _process_messsages_and_replace_long_urls_shorter_ones(self, input_messages: list[BaseMessage]) -> dict[str, str]:
		"""Replace long URLs with shorter ones
		? @dev edits input_messages in place

		returns:
			tuple[filtered_input_messages, urls we replaced {shorter_url: original_url}]
		"""
		from browser_use.llm.messages import AssistantMessage, UserMessage

		urls_replaced: dict[str, str] = {}

		# 各メッセージをその場で処理
		for message in input_messages:
			# SystemMessage は自前で制御しているので処理不要
			if isinstance(message, (UserMessage, AssistantMessage)):
				if isinstance(message.content, str):
					# 単純な文字列コンテンツ
					message.content, replaced_urls = self._replace_urls_in_text(message.content)
					urls_replaced.update(replaced_urls)

				elif isinstance(message.content, list):
					# コンテンツパーツのリスト
					for part in message.content:
						if isinstance(part, ContentPartTextParam):
							part.text, replaced_urls = self._replace_urls_in_text(part.text)
							urls_replaced.update(replaced_urls)

		return urls_replaced

	@staticmethod
	def _recursive_process_all_strings_inside_pydantic_model(model: BaseModel, url_replacements: dict[str, str]) -> None:
		"""Recursively process all strings inside a Pydantic model, replacing shortened URLs with originals in place."""
		for field_name, field_value in model.__dict__.items():
			if isinstance(field_value, str):
				# 短縮された URL を元の URL に戻す
				processed_string = Agent._replace_shortened_urls_in_string(field_value, url_replacements)
				setattr(model, field_name, processed_string)
			elif isinstance(field_value, BaseModel):
				# ネストされた Pydantic モデル内の文字列を再帰的に処理
				Agent._recursive_process_all_strings_inside_pydantic_model(field_value, url_replacements)
			elif isinstance(field_value, dict):
				# 辞書はその場で処理
				Agent._recursive_process_dict(field_value, url_replacements)
			elif isinstance(field_value, (list, tuple)):
				processed_value = Agent._recursive_process_list_or_tuple(field_value, url_replacements)
				setattr(model, field_name, processed_value)

	@staticmethod
	def _recursive_process_dict(dictionary: dict, url_replacements: dict[str, str]) -> None:
		"""Helper method to process dictionaries."""
		for k, v in dictionary.items():
			if isinstance(v, str):
				dictionary[k] = Agent._replace_shortened_urls_in_string(v, url_replacements)
			elif isinstance(v, BaseModel):
				Agent._recursive_process_all_strings_inside_pydantic_model(v, url_replacements)
			elif isinstance(v, dict):
				Agent._recursive_process_dict(v, url_replacements)
			elif isinstance(v, (list, tuple)):
				dictionary[k] = Agent._recursive_process_list_or_tuple(v, url_replacements)

	@staticmethod
	def _recursive_process_list_or_tuple(container: list | tuple, url_replacements: dict[str, str]) -> list | tuple:
		"""Helper method to process lists and tuples."""
		if isinstance(container, tuple):
			# タプルの場合は処理済み要素で新しいタプルを作成
			processed_items = []
			for item in container:
				if isinstance(item, str):
					processed_items.append(Agent._replace_shortened_urls_in_string(item, url_replacements))
				elif isinstance(item, BaseModel):
					Agent._recursive_process_all_strings_inside_pydantic_model(item, url_replacements)
					processed_items.append(item)
				elif isinstance(item, dict):
					Agent._recursive_process_dict(item, url_replacements)
					processed_items.append(item)
				elif isinstance(item, (list, tuple)):
					processed_items.append(Agent._recursive_process_list_or_tuple(item, url_replacements))
				else:
					processed_items.append(item)
			return tuple(processed_items)
		else:
			# リストの場合はその場で書き換える
			for i, item in enumerate(container):
				if isinstance(item, str):
					container[i] = Agent._replace_shortened_urls_in_string(item, url_replacements)
				elif isinstance(item, BaseModel):
					Agent._recursive_process_all_strings_inside_pydantic_model(item, url_replacements)
				elif isinstance(item, dict):
					Agent._recursive_process_dict(item, url_replacements)
				elif isinstance(item, (list, tuple)):
					container[i] = Agent._recursive_process_list_or_tuple(item, url_replacements)
			return container

	@staticmethod
	def _replace_shortened_urls_in_string(text: str, url_replacements: dict[str, str]) -> str:
		"""Replace all shortened URLs in a string with their original URLs."""
		result = text
		for shortened_url, original_url in url_replacements.items():
			result = result.replace(shortened_url, original_url)
		return result

	# endregion - URL 置換処理終了

	@time_execution_async('--get_next_action')
	@observe_debug(ignore_input=True, ignore_output=True, name='get_model_output')
	async def get_model_output(self, input_messages: list[BaseMessage]) -> AgentOutput:
		"""Get next action from LLM based on current state"""

		urls_replaced = self._process_messsages_and_replace_long_urls_shorter_ones(input_messages)

		# ainvoke に渡すキーワード引数を構築
		# 注意: ChatBrowserUse は output_format のスキーマから自動的にアクション説明を生成する
		kwargs: dict = {'output_format': self.AgentOutput}

		try:
			response = await self.llm.ainvoke(input_messages, **kwargs)
			parsed: AgentOutput = response.completion  # type: ignore[assignment]

			# モデル応答内の短縮 URL を元の URL に戻す
			if urls_replaced:
				self._recursive_process_all_strings_inside_pydantic_model(parsed, urls_replaced)

			# 必要であればアクション数を max_actions_per_step までに制限
			if len(parsed.action) > self.settings.max_actions_per_step:
				parsed.action = parsed.action[: self.settings.max_actions_per_step]

			if not (hasattr(self.state, 'paused') and (self.state.paused or self.state.stopped)):
				log_response(parsed, self.tools.registry.registry, self.logger)

			self._log_next_action_summary(parsed)
			return parsed
		except ValidationError:
			# Pydantic の検証エラーは十分情報を含むのでそのまま再送出
			raise

	async def _log_agent_run(self) -> None:
		"""Log the agent run"""
		# タスク名は青色で表示
		self.logger.info(f'\033[34m🚀 Task: {self.task}\033[0m')

		self.logger.debug(f'🤖 Browser-Use Library Version {self.version} ({self.source})')

		# 新しいバージョンがある場合はアップデート案内を表示
		latest_version = await check_latest_browser_use_version()
		if latest_version and latest_version != self.version:
			self.logger.info(
				f'📦 Newer version available: {latest_version} (current: {self.version}). Upgrade with: uv add browser-use@{latest_version}'
			)

	def _log_first_step_startup(self) -> None:
		"""Log startup message only on the first step"""
		if len(self.history.history) == 0:
			self.logger.info(f'🧠 Starting a browser-use agent with version {self.version} and model={self.llm.model}')

	def _log_step_context(self, browser_state_summary: BrowserStateSummary) -> None:
		"""Log step context information"""
		url = browser_state_summary.url if browser_state_summary else ''
		url_short = url[:50] + '...' if len(url) > 50 else url
		interactive_count = len(browser_state_summary.dom_state.selector_map) if browser_state_summary else 0
		self.logger.info('\n')
		self.logger.info(f'📍 Step {self.state.n_steps}:')
		self.logger.debug(f'Evaluating page with {interactive_count} interactive elements on: {url_short}')

	def _log_next_action_summary(self, parsed: 'AgentOutput') -> None:
		"""Log a comprehensive summary of the next action(s)"""
		if not (self.logger.isEnabledFor(logging.DEBUG) and parsed.action):
			return

		action_count = len(parsed.action)

		# アクションの詳細を集計
		action_details = []
		for i, action in enumerate(parsed.action):
			action_data = action.model_dump(exclude_unset=True)
			action_name = next(iter(action_data.keys())) if action_data else 'unknown'
			action_params = action_data.get(action_name, {}) if action_data else {}

			# 主要パラメータを簡潔に整形
			param_summary = []
			if isinstance(action_params, dict):
				for key, value in action_params.items():
					if key == 'index':
						param_summary.append(f'#{value}')
					elif key == 'text' and isinstance(value, str):
						text_preview = value[:30] + '...' if len(value) > 30 else value
						param_summary.append(f'text="{text_preview}"')
					elif key == 'url':
						param_summary.append(f'url="{value}"')
					elif key == 'success':
						param_summary.append(f'success={value}')
					elif isinstance(value, (str, int, bool)):
						val_str = str(value)[:30] + '...' if len(str(value)) > 30 else str(value)
						param_summary.append(f'{key}={val_str}')

			param_str = f'({", ".join(param_summary)})' if param_summary else ''
			action_details.append(f'{action_name}{param_str}')

		# 単一アクションか複数アクションかで出力を変える
		if action_count == 1:
			self.logger.info(f'☝️ Decided next action: {action_name}{param_str}')
		else:
			summary_lines = [f'✌️ Decided next {action_count} multi-actions:']
			for i, detail in enumerate(action_details):
				summary_lines.append(f'          {i + 1}. {detail}')
			self.logger.info('\n'.join(summary_lines))

	def _log_step_completion_summary(self, step_start_time: float, result: list[ActionResult]) -> None:
		"""Log step completion summary with action count, timing, and success/failure stats"""
		if not result:
			return

		step_duration = time.time() - step_start_time
		action_count = len(result)

		# 成功・失敗件数を集計
		success_count = sum(1 for r in result if not r.error)
		failure_count = action_count - success_count

		# 成功／失敗の表示用テキストを作成
		success_indicator = f'✅ {success_count}' if success_count > 0 else ''
		failure_indicator = f'❌ {failure_count}' if failure_count > 0 else ''
		status_parts = [part for part in [success_indicator, failure_indicator] if part]
		status_str = ' | '.join(status_parts) if status_parts else '✅ 0'

		self.logger.debug(
			f'📍 Step {self.state.n_steps}: Ran {action_count} action{"" if action_count == 1 else "s"} in {step_duration:.2f}s: {status_str}'
		)

	def _log_agent_event(self, max_steps: int, agent_run_error: str | None = None) -> None:
		"""Sent the agent event for this run to telemetry"""

		token_summary = self.token_cost_service.get_usage_tokens_for_model(self.llm.model)

		# action_history 用データを組み立てる
		action_history_data = []
		for item in self.history.history:
			if item.model_output and item.model_output.action:
				# 各ステップの ActionModel を辞書形式へ変換
				step_actions = [
					action.model_dump(exclude_unset=True)
					for action in item.model_output.action
					if action  # リスト内に None が許される場合でも None 以外のみ残す
				]
				action_history_data.append(step_actions)
			else:
				# アクションや出力が無いステップは None を追加
				action_history_data.append(None)

		final_res = self.history.final_result()
		final_result_str = json.dumps(final_res) if final_res is not None else None

		self.telemetry.capture(
			AgentTelemetryEvent(
				task=self.task,
				model=self.llm.model,
				model_provider=self.llm.provider,
				max_steps=max_steps,
				max_actions_per_step=self.settings.max_actions_per_step,
				use_vision=self.settings.use_vision,
				version=self.version,
				source=self.source,
				cdp_url=urlparse(self.browser_session.cdp_url).hostname
				if self.browser_session and self.browser_session.cdp_url
				else None,
				action_errors=self.history.errors(),
				action_history=action_history_data,
				urls_visited=self.history.urls(),
				steps=self.state.n_steps,
				total_input_tokens=token_summary.prompt_tokens,
				total_duration_seconds=self.history.total_duration_seconds(),
				success=self.history.is_successful(),
				final_result_response=final_result_str,
				error_message=agent_run_error,
			)
		)

	async def take_step(self, step_info: AgentStepInfo | None = None) -> tuple[bool, bool]:
		"""Take a step

		Returns:
		        Tuple[bool, bool]: (is_done, is_valid)
		"""
		if step_info is not None and step_info.step_number == 0:
			# 初回ステップ
			self._log_first_step_startup()
			# 通常は try-catch を入れていなかったが、コールバックが InterruptedError を送る場合があるため握りつぶす
			try:
				await self._execute_initial_actions()
			except InterruptedError:
				pass
			except Exception as e:
				raise e

		await self.step(step_info)

		if self.history.is_done():
			await self.log_completion()
			if self.register_done_callback:
				if inspect.iscoroutinefunction(self.register_done_callback):
					await self.register_done_callback(self.history)
				else:
					self.register_done_callback(self.history)
			return True, True

		return False, False

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
			self._log_agent_event(max_steps=max_steps, agent_run_error='SIGINT: Cancelled by user')
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
			await self._log_agent_run()

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
			self._log_first_step_startup()
			# ブラウザセッションを開始しウォッチドッグを取り付ける
			await self.browser_session.start()

			# 本来 try-catch は不要だがコールバックが InterruptedError を送る可能性がある
			try:
				await self._execute_initial_actions()
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
					await self.log_completion()

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
					self._log_agent_event(max_steps=max_steps, agent_run_error=agent_run_error)
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

	@observe_debug(ignore_input=True, ignore_output=True)
	@time_execution_async('--multi_act')
	async def multi_act(
		self,
		actions: list[ActionModel],
		check_for_new_elements: bool = True,
	) -> list[ActionResult]:
		"""Execute multiple actions"""
		results: list[ActionResult] = []
		time_elapsed = 0
		total_actions = len(actions)

		assert self.browser_session is not None, 'BrowserSession is not set up'
		try:
			if (
				self.browser_session._cached_browser_state_summary is not None
				and self.browser_session._cached_browser_state_summary.dom_state is not None
			):
				cached_selector_map = dict(self.browser_session._cached_browser_state_summary.dom_state.selector_map)
				cached_element_hashes = {e.parent_branch_hash() for e in cached_selector_map.values()}
			else:
				cached_selector_map = {}
				cached_element_hashes = set()
		except Exception as e:
			self.logger.error(f'Error getting cached selector map: {e}')
			cached_selector_map = {}
			cached_element_hashes = set()

		for i, action in enumerate(actions):
			if i > 0:
				# `done` は単独アクションとしてのみ許可する
				if action.model_dump(exclude_unset=True).get('done') is not None:
					msg = f'Done action is allowed only as a single action - stopped after action {i} / {total_actions}.'
					self.logger.debug(msg)
					break

			# DOM 同期チェック: 1つ目以降のアクションで要素インデックスが有効か確認
			# これによりスタイルエラーを防ぎつつ、実行前の余計なリフレッシュは避ける
			if action.get_index() is not None and i != 0:
				new_browser_state_summary = await self.browser_session.get_browser_state_summary(
					include_screenshot=False,
				)
				new_selector_map = new_browser_state_summary.dom_state.selector_map

				# Detect index change after previous action
				orig_target = cached_selector_map.get(action.get_index())
				orig_target_hash = orig_target.parent_branch_hash() if orig_target else None

				new_target = new_selector_map.get(action.get_index())  # type: ignore
				new_target_hash = new_target.parent_branch_hash() if new_target else None

				def get_remaining_actions_str(actions: list[ActionModel], index: int) -> str:
					remaining_actions = []
					for remaining_action in actions[index:]:
						action_data = remaining_action.model_dump(exclude_unset=True)
						action_name = next(iter(action_data.keys())) if action_data else 'unknown'
						remaining_actions.append(action_name)
					return ', '.join(remaining_actions)

				if orig_target_hash != new_target_hash:
					# 実行されない残りのアクション名を取得
					remaining_actions_str = get_remaining_actions_str(actions, i)
					msg = f'Page changed after action: actions {remaining_actions_str} are not yet executed'
					logger.info(msg)
					results.append(
						ActionResult(
							extracted_content=msg,
							include_in_memory=True,
							long_term_memory=msg,
						)
					)
					break

				# 新しく出現した要素がないか確認
				new_element_hashes = {e.parent_branch_hash() for e in new_selector_map.values()}
				if check_for_new_elements and not new_element_hashes.issubset(cached_element_hashes):
					# 次のアクションが index 指定を要求するがページに新要素がある場合
					# 要素数の差分をデバッグログに残す
					self.logger.debug(f'New elements: {abs(len(new_element_hashes) - len(cached_element_hashes))}')
					remaining_actions_str = get_remaining_actions_str(actions, i)
					msg = f'Something new appeared after action {i} / {total_actions}: actions {remaining_actions_str} were not executed'
					logger.info(msg)
					results.append(
						ActionResult(
							extracted_content=msg,
							include_in_memory=True,
							long_term_memory=msg,
						)
					)
					break

			# アクション間に待機時間を挟む（2つ目以降）
			if i > 0:
				await asyncio.sleep(self.browser_profile.wait_between_actions)

			red = '\033[91m'
			green = '\033[92m'
			cyan = '\033[96m'
			blue = '\033[34m'
			reset = '\033[0m'

			try:
				await self._check_stop_or_pause()
				# アクション名を取得
				action_data = action.model_dump(exclude_unset=True)
				action_name = next(iter(action_data.keys())) if action_data else 'unknown'
				action_params = getattr(action, action_name, '') or str(action.model_dump(mode='json'))[:140].replace(
					'"', ''
				).replace('{', '').replace('}', '').replace("'", '').strip().strip(',')
				# 長さを確認する前に必ず文字列化する
				action_params = str(action_params)
				action_params = f'{action_params[:522]}...' if len(action_params) > 528 else action_params
				time_start = time.time()
				self.logger.info(f'  🦾 {blue}[ACTION {i + 1}/{total_actions}]{reset} {action_params}')

				result = await self.tools.act(
					action=action,
					browser_session=self.browser_session,
					file_system=self.file_system,
					page_extraction_llm=self.settings.page_extraction_llm,
					sensitive_data=self.sensitive_data,
					available_file_paths=self.available_file_paths,
				)

				time_end = time.time()
				time_elapsed = time_end - time_start
				results.append(result)

				self.logger.debug(
					f'☑️ Executed action {i + 1}/{total_actions}: {green}{action_params}{reset} in {time_elapsed:.2f}s'
				)

				if results[-1].is_done or results[-1].error or i == total_actions - 1:
					break

			except Exception as e:
				# アクション実行中の例外を処理
				self.logger.error(f'❌ Executing action {i + 1} failed -> {type(e).__name__}: {e}')
				raise e

		return results

	async def log_completion(self) -> None:
		"""Log the completion of the task"""
		# self._task_end_time = time.time()
		# self._task_duration = self._task_end_time - self._task_start_time  # TODO: take_step 使用時に正しく動かないため要調整
		if self.history.is_successful():
			self.logger.info('✅ Task completed successfully')
		else:
			self.logger.info('❌ Task completed without success')

	async def rerun_history(
		self,
		history: AgentHistoryList,
		max_retries: int = 3,
		skip_failures: bool = True,
		delay_between_actions: float = 2.0,
	) -> list[ActionResult]:
		"""
		Rerun a saved history of actions with error handling and retry logic.

		Args:
		                history: The history to replay
		                max_retries: Maximum number of retries per action
		                skip_failures: Whether to skip failed actions or stop execution
		                delay_between_actions: Delay between actions in seconds

		Returns:
		                List of action results
		"""
		# 再実行時は新規セッション扱いにしないためクラウド同期イベントを送らない
		self.state.session_initialized = True

		# ブラウザセッションを初期化
		await self.browser_session.start()

		results = []

		for i, history_item in enumerate(history.history):
			goal = history_item.model_output.current_state.next_goal if history_item.model_output else ''
			step_num = history_item.metadata.step_number if history_item.metadata else i
			step_name = 'Initial actions' if step_num == 0 else f'Step {step_num}'
			self.logger.info(f'Replaying {step_name} ({i + 1}/{len(history.history)}): {goal}')

			if (
				not history_item.model_output
				or not history_item.model_output.action
				or history_item.model_output.action == [None]
			):
				self.logger.warning(f'{step_name}: No action to replay, skipping')
				results.append(ActionResult(error='No action to replay'))
				continue

			retry_count = 0
			while retry_count < max_retries:
				try:
					result = await self._execute_history_step(history_item, delay_between_actions)
					results.extend(result)
					break

				except Exception as e:
					retry_count += 1
					if retry_count == max_retries:
						error_msg = f'{step_name} failed after {max_retries} attempts: {str(e)}'
						self.logger.error(error_msg)
						if not skip_failures:
							results.append(ActionResult(error=error_msg))
							raise RuntimeError(error_msg)
					else:
						self.logger.warning(f'{step_name} failed (attempt {retry_count}/{max_retries}), retrying...')
						await asyncio.sleep(delay_between_actions)

		await self.close()
		return results

	async def _execute_initial_actions(self) -> None:
		# 初期アクションが設定されていれば実行する
		if self.initial_actions and not self.state.follow_up_task:
			self.logger.debug(f'⚡ Executing {len(self.initial_actions)} initial actions...')
			result = await self.multi_act(self.initial_actions, check_for_new_elements=False)
			# 結果の1件目に自動ロードした旨を追記
			if result and self.initial_url and result[0].long_term_memory:
				result[0].long_term_memory = f'Found initial url and automatically loaded it. {result[0].long_term_memory}'
			self.state.last_result = result

			# 再実行に備えて初期アクションをステップ0として履歴に保存
			# 初期アクションは通常 URL 遷移のみなのでブラウザ状態のキャプチャは省略
			model_output = self.AgentOutput(
				evaluation_previous_goal='Starting agent with initial actions',
				memory='',
				next_goal='Execute initial navigation or setup actions',
				action=self.initial_actions,
			)

			metadata = StepMetadata(
				step_number=0,
				step_start_time=time.time(),
				step_end_time=time.time(),
			)

			# 初期アクション用に最小限のブラウザ状態を作成
			state_history = BrowserStateHistory(
				url=self.initial_url or '',
				title='Initial Actions',
				tabs=[],
				interacted_element=[None] * len(self.initial_actions),  # DOM 情報は不要
				screenshot_path=None,
			)

			history_item = AgentHistory(
				model_output=model_output,
				result=result,
				state=state_history,
				metadata=metadata,
			)

			self.history.add_item(history_item)
			self.logger.debug('📝 Saved initial actions to history as step 0')
			self.logger.debug('Initial actions completed')

	async def _execute_history_step(self, history_item: AgentHistory, delay: float) -> list[ActionResult]:
		"""Execute a single step from history with element validation"""
		assert self.browser_session is not None, 'BrowserSession is not set up'
		state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
		if not state or not history_item.model_output:
			raise ValueError('Invalid state or model output')
		updated_actions = []
		for i, action in enumerate(history_item.model_output.action):
			updated_action = await self._update_action_indices(
				history_item.state.interacted_element[i],
				action,
				state,
			)
			updated_actions.append(updated_action)

			if updated_action is None:
				raise ValueError(f'Could not find matching element {i} in current page')

		result = await self.multi_act(updated_actions)

		await asyncio.sleep(delay)
		return result

	async def _update_action_indices(
		self,
		historical_element: DOMInteractedElement | None,
		action: ActionModel,  # ご利用のアクションモデルに合わせて適切に型付けする
		browser_state_summary: BrowserStateSummary,
	) -> ActionModel | None:
		"""
		Update action indices based on current page state.
		Returns updated action or None if element cannot be found.
		"""
		if not historical_element or not browser_state_summary.dom_state.selector_map:
			return action

		# selector_hash_map = {hash(e): e for e in browser_state_summary.dom_state.selector_map.values()}  # 参照用の例

		highlight_index, current_element = next(
			(
				(highlight_index, element)
				for highlight_index, element in browser_state_summary.dom_state.selector_map.items()
				if element.element_hash == historical_element.element_hash
			),
			(None, None),
		)

		if not current_element or highlight_index is None:
			return None

		old_index = action.get_index()
		if old_index != highlight_index:
			action.set_index(highlight_index)
			self.logger.info(f'Element moved in DOM, updated index from {old_index} to {highlight_index}')

		return action

	async def load_and_rerun(self, history_file: str | Path | None = None, **kwargs) -> list[ActionResult]:
		"""
		Load history from file and rerun it.

		Args:
		                history_file: Path to the history file
		                **kwargs: Additional arguments passed to rerun_history
		"""
		if not history_file:
			history_file = 'AgentHistory.json'
		history = AgentHistoryList.load_from_file(history_file, self.AgentOutput)
		return await self.rerun_history(history, **kwargs)

	def save_history(self, file_path: str | Path | None = None) -> None:
		"""Save the history to a file with sensitive data filtering"""
		if not file_path:
			file_path = 'AgentHistory.json'
		self.history.save_to_file(file_path, sensitive_data=self.sensitive_data)

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
