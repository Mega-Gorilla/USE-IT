from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6 import QtCore

from browser_use import Agent
from browser_use.agent.config import AgentConfig
from browser_use.agent.views import AgentOutput, AgentStepInfo, ApprovalResult
from browser_use.browser import BrowserProfile
from browser_use.browser.views import BrowserStateSummary
from browser_use.config import CONFIG
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.base import BaseChatModel
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.openai.chat import ChatOpenAI


class LogEmitter(QtCore.QObject):
	message = QtCore.Signal(str)


class QtLogHandler(logging.Handler):
	"""Forward logging records to the GUI via Qt signals."""

	def __init__(self) -> None:
		super().__init__()
		self._emitter = LogEmitter()

	@property
	def signal(self):
		return self._emitter.message

	def emit(self, record: logging.LogRecord) -> None:
		try:
			msg = self.format(record)
		except Exception:
			self.handleError(record)
			return

		self._emitter.message.emit(msg)


@dataclass(slots=True)
class UserPreferences:
	task: str
	max_steps: int = 100


class AgentWorker(QtCore.QThread):
	status = QtCore.Signal(str)
	progress = QtCore.Signal(int, int)
	finished = QtCore.Signal(bool, str)
	step_update = QtCore.Signal(dict)
	approval_requested = QtCore.Signal(dict)

	def __init__(self, preferences: UserPreferences, parent: QtCore.QObject | None = None) -> None:
		super().__init__(parent)
		self._preferences = preferences
		self._cancel_requested = False
		self._loop: asyncio.AbstractEventLoop | None = None
		self._agent: Agent[Any, Any] | None = None
		self._raw_config: dict[str, Any] = CONFIG.load_config()
		self._agent_config: AgentConfig = self._build_agent_config()
		self._pending_approval_future: asyncio.Future[ApprovalResult] | None = None

	def run(self) -> None:  # noqa: D401
		"""Qt thread entrypoint."""
		logger = logging.getLogger(__name__)
		self.status.emit('初期化中…')
		self._loop = asyncio.new_event_loop()
		asyncio.set_event_loop(self._loop)

		try:
			self._loop.run_until_complete(self._execute())
		except asyncio.CancelledError:
			self.finished.emit(False, 'ユーザーによりキャンセルされました')
		except Exception as exc:  # pragma: no cover - GUI side-effects
			logger.exception('GUI worker failed', exc_info=exc)
			self.finished.emit(False, str(exc))
		else:
			self.finished.emit(True, 'タスクが完了しました')
		finally:
			try:
				self._loop.run_until_complete(self._cleanup())
			except Exception as cleanup_exc:  # pragma: no cover - cleanup best-effort
				logger.warning(f'Cleanup error: {cleanup_exc}')

			try:
				self._loop.run_until_complete(self._loop.shutdown_asyncgens())
			except Exception:
				pass
			asyncio.set_event_loop(None)
			self._loop.close()
			self._loop = None
			self._agent = None

	def request_cancel(self) -> None:
		self._cancel_requested = True

		agent = self._agent
		loop = self._loop

		if agent is not None and loop is not None:
			loop.call_soon_threadsafe(agent.stop)
		self._finish_pending_approval(ApprovalResult(decision='cancel'))

	async def _execute(self) -> None:
		task = self._preferences.task.strip()
		if not task:
			raise ValueError('タスク内容が空です。')

		current_config = CONFIG.load_config()
		if current_config != self._raw_config:
			self._raw_config = current_config
			self._agent_config = self._build_agent_config()

		self._agent_config.task = task

		agent = Agent(config=self._agent_config)
		self._agent = agent

		async def on_step_end(agent_ref: Agent[Any, Any]) -> None:
			current = agent_ref.state.n_steps
			total = getattr(agent_ref.settings, 'max_steps', self._preferences.max_steps)
			self.progress.emit(current, total)

			if self._cancel_requested:
				agent_ref.stop()
				raise asyncio.CancelledError('User requested cancellation')

		self.status.emit('エージェントを起動しています…')
		await agent.run(max_steps=self._preferences.max_steps, on_step_end=on_step_end)

	async def _cleanup(self) -> None:
		"""Ensure agent resources are released."""
		if self._agent is None:
			return

		agent = self._agent
		try:
			await asyncio.wait_for(agent.close(), timeout=5.0)
		except asyncio.TimeoutError:
			logging.getLogger(__name__).warning('ブラウザクリーンアップがタイムアウトしました')
		except Exception as exc:
			logging.getLogger(__name__).warning(f'クリーンアップエラー: {exc}')

	def _default_language(self) -> str:
		agent_cfg = self._raw_config.get('agent', {})
		return agent_cfg.get('language', 'en')

	def _interactive_mode_enabled(self) -> bool:
		agent_cfg = self._raw_config.get('agent', {})
		return bool(agent_cfg.get('interactive_mode', False))

	def _resolve_llm(self) -> BaseChatModel:
		llm_cfg = self._raw_config.get('llm', {})
		model_name = llm_cfg.get('model', '')

		if not model_name:
			raise RuntimeError('LLMモデルが設定されていません。設定ファイルか環境変数を確認してください。')

		api_key = llm_cfg.get('api_key')
		placeholder_keys = {
			'your-openai-api-key-here',
			'your-anthropic-api-key-here',
			'your-google-api-key-here',
		}
		model_lower = model_name.lower()

		def _ensure_key(value: str | None, provider: str) -> str:
			match = value or ''
			if not match or match in placeholder_keys:
				raise RuntimeError(f'{provider} の API キーが設定されていません。環境変数または config を確認してください。')
			return match

		if 'gemini' in model_lower:
			key = api_key or CONFIG.GOOGLE_API_KEY
			return ChatGoogle(model=model_name, api_key=_ensure_key(key, 'Google Gemini'))

		if model_lower.startswith('claude') or 'claude' in model_lower:
			key = api_key or CONFIG.ANTHROPIC_API_KEY
			return ChatAnthropic(model=model_name, api_key=_ensure_key(key, 'Anthropic Claude'))

		key = api_key or CONFIG.OPENAI_API_KEY
		return ChatOpenAI(model=model_name, api_key=_ensure_key(key, 'OpenAI'))

	def _build_browser_profile(self) -> BrowserProfile:
		profile_cfg = self._raw_config.get('browser_profile', {})
		gui_profile_dir = CONFIG.BROWSER_USE_PROFILES_DIR / 'gui'
		gui_profile_dir.mkdir(parents=True, exist_ok=True)

		allowed_keys = set(BrowserProfile.model_fields.keys())
		filtered: dict[str, Any] = {key: value for key, value in profile_cfg.items() if key in allowed_keys and value is not None}

		# GUI用のプロファイルディレクトリをデフォルトにする
		filtered.setdefault('user_data_dir', str(gui_profile_dir))
		filtered.setdefault('headless', False)
		# GUIではタスク完了時にブラウザを自動で閉じたいので keep_alive を強制的に無効化する
		filtered['keep_alive'] = False

		if 'downloads_path' not in filtered:
			downloads_dir = Path(CONFIG.BROWSER_USE_DOWNLOADS_DIR) / 'gui'
			downloads_dir.mkdir(parents=True, exist_ok=True)
			filtered['downloads_path'] = str(downloads_dir)

		return BrowserProfile(**filtered)

	def _build_agent_config(self) -> AgentConfig:
		llm = self._resolve_llm()
		browser_profile = self._build_browser_profile()
		interactive_mode = self._interactive_mode_enabled()
		return AgentConfig(
			task=self._preferences.task,
			llm=llm,
			browser_profile=browser_profile,
			interactive_mode=interactive_mode,
			approval_callback=self._approval_callback if interactive_mode else None,
			language=self._default_language(),
			register_new_step_callback=self._handle_step_update,
		)

	@property
	def agent_config(self) -> AgentConfig:
		return self._agent_config

	@property
	def llm_summary(self) -> dict[str, Any]:
		llm_cfg = self._raw_config.get('llm', {})
		return {
			'model': self._agent_config.llm.model if self._agent_config.llm else llm_cfg.get('model', '不明'),
			'temperature': llm_cfg.get('temperature', '—'),
			'use_thinking': self._agent_config.use_thinking,
		}

	@property
	def browser_summary(self) -> dict[str, Any]:
		profile = self._agent_config.browser_profile
		return {
			'headless': bool(getattr(profile, 'headless', False)),
			'keep_alive': bool(getattr(profile, 'keep_alive', False)),
			'proxy': getattr(profile, 'proxy', None),
		}

	def _handle_step_update(
		self,
		browser_state_summary: BrowserStateSummary,
		model_output: AgentOutput,
		step_number: int,
	) -> None:
		max_steps = self._preferences.max_steps
		if self._agent and getattr(self._agent, 'settings', None):
			max_steps = getattr(self._agent.settings, 'max_steps', max_steps)

		payload = {
			'step_number': step_number,
			'max_steps': max_steps,
			'url': browser_state_summary.url,
			'title': browser_state_summary.title,
			'thinking': model_output.thinking,
			'evaluation_previous_goal': model_output.evaluation_previous_goal,
			'memory': model_output.memory,
			'next_goal': model_output.next_goal,
			'actions': self._summarize_actions(model_output),
		}
		self.step_update.emit(payload)

	@staticmethod
	def _summarize_actions(model_output: AgentOutput) -> list[str]:
		summaries: list[str] = []
		for action in model_output.action:
			data = action.model_dump(exclude_unset=True)
			if not data:
				continue
			action_name, params = next(iter(data.items()))
			if not params:
				summaries.append(action_name)
				continue
			param_pairs = []
			for key, value in params.items():
				param_pairs.append(f'{key}={value}')
			summary = f'{action_name}({", ".join(param_pairs)})'
			summaries.append(summary)
		return summaries

	def submit_approval_result(self, result: ApprovalResult) -> None:
		"""Receive approval results from the GUI thread safely."""
		self._finish_pending_approval(result)

	async def _approval_callback(
		self,
		step_info: AgentStepInfo | None,
		model_output: AgentOutput,
		browser_state_summary: BrowserStateSummary,
	) -> ApprovalResult:
		if self._loop is None:
			raise RuntimeError('イベントループが初期化されていません。')

		if self._cancel_requested:
			return ApprovalResult(decision='cancel')

		payload = self._build_approval_payload(step_info, model_output, browser_state_summary)
		future: asyncio.Future[ApprovalResult] = self._loop.create_future()
		self._pending_approval_future = future
		self.approval_requested.emit(payload)

		try:
			result = await future
		finally:
			self._pending_approval_future = None
		return result

	def _finish_pending_approval(self, result: ApprovalResult) -> None:
		loop = self._loop
		future = self._pending_approval_future

		if loop is None or future is None:
			return

		def _resolve() -> None:
			if not future.done():
				future.set_result(result)

		loop.call_soon_threadsafe(_resolve)

	def _build_approval_payload(
		self,
		step_info: AgentStepInfo | None,
		model_output: AgentOutput,
		browser_state_summary: BrowserStateSummary,
	) -> dict[str, Any]:
		step_number = getattr(step_info, 'step_number', None)
		max_steps = self._preferences.max_steps
		if self._agent and getattr(self._agent, 'settings', None):
			max_steps = getattr(self._agent.settings, 'max_steps', max_steps)

		return {
			'step_number': step_number,
			'max_steps': max_steps,
			'url': browser_state_summary.url,
			'title': browser_state_summary.title,
			'screenshot': browser_state_summary.screenshot,
			'next_goal': model_output.next_goal,
			'actions': self._summarize_actions(model_output),
			'thinking': model_output.thinking,
		}
