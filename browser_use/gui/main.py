from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets
from dotenv import load_dotenv

load_dotenv()

from browser_use import Agent
from browser_use.agent.config import AgentConfig
from browser_use.browser import BrowserProfile
from browser_use.config import CONFIG
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.base import BaseChatModel


# ユーザー設定を表現する軽量データクラス
@dataclass(slots=True)
class UserPreferences:
	task: str
	max_steps: int = 100


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


class AgentWorker(QtCore.QThread):
	status = QtCore.Signal(str)
	progress = QtCore.Signal(int, int)
	finished = QtCore.Signal(bool, str)

	def __init__(self, preferences: UserPreferences, parent: QtCore.QObject | None = None) -> None:
		super().__init__(parent)
		self._preferences = preferences
		self._cancel_requested = False
		self._loop: asyncio.AbstractEventLoop | None = None
		self._agent: Agent[Any, Any] | None = None

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

	async def _execute(self) -> None:
		task = self._preferences.task.strip()
		if not task:
			raise ValueError('タスク内容が空です。')

		config = CONFIG.load_config()
		llm = self._resolve_llm(config)
		browser_profile = self._build_browser_profile(config)

		config = AgentConfig(
			task=task,
			llm=llm,
			browser_profile=browser_profile,
			interactive_mode=False,
			language=self._default_language(config),
		)
		agent = Agent(config=config)
		self._agent = agent

		async def on_step_end(agent_ref: Agent[Any, Any]) -> None:
			current = agent_ref.state.n_steps
			total = agent_ref.settings.max_steps
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

	def _default_language(self, config: dict[str, Any]) -> str:
		agent_cfg = config.get('agent', {})
		return agent_cfg.get('language', 'en')

	def _resolve_llm(self, config: dict[str, Any]) -> BaseChatModel:
		llm_cfg = config.get('llm', {})
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

	def _build_browser_profile(self, config: dict[str, Any]) -> BrowserProfile:
		profile_cfg = config.get('browser_profile', {})
		gui_profile_dir = CONFIG.BROWSER_USE_PROFILES_DIR / 'gui'
		gui_profile_dir.mkdir(parents=True, exist_ok=True)

		allowed_keys = set(BrowserProfile.model_fields.keys())
		filtered: dict[str, Any] = {key: value for key, value in profile_cfg.items() if key in allowed_keys and value is not None}

		# GUI用のプロファイルディレクトリをデフォルトにする
		filtered.setdefault('user_data_dir', str(gui_profile_dir))
		filtered.setdefault('headless', False)

		if 'downloads_path' not in filtered:
			downloads_dir = Path(CONFIG.BROWSER_USE_DOWNLOADS_DIR) / 'gui'
			downloads_dir.mkdir(parents=True, exist_ok=True)
			filtered['downloads_path'] = str(downloads_dir)

		return BrowserProfile(**filtered)


class MainWindow(QtWidgets.QMainWindow):
	def __init__(self) -> None:
		super().__init__()
		self.setWindowTitle('Browser Use GUI')
		self.resize(960, 720)

		self._worker: AgentWorker | None = None
		self._log_handler = QtLogHandler()
		self._log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
		self._log_handler.signal.connect(self._append_log)
		self._handler_attached = False
		self._closing_after_stop = False

		self._setup_ui()
		self._setup_menu()
		self._update_controls(running=False)

	def _setup_ui(self) -> None:
		central = QtWidgets.QWidget()
		layout = QtWidgets.QVBoxLayout(central)

		task_label = QtWidgets.QLabel('タスクの内容')
		self.task_input = QtWidgets.QPlainTextEdit()
		self.task_input.setPlaceholderText('例: Amazonで最新のノイズキャンセリングヘッドフォンを探してレポートしてください')

		controls_layout = QtWidgets.QHBoxLayout()
		self.run_button = QtWidgets.QPushButton('実行')
		self.stop_button = QtWidgets.QPushButton('停止')
		self.clear_button = QtWidgets.QPushButton('ログをクリア')

		controls_layout.addWidget(self.run_button)
		controls_layout.addWidget(self.stop_button)
		controls_layout.addWidget(self.clear_button)
		controls_layout.addStretch(1)

		log_label = QtWidgets.QLabel('ログ')
		self.log_view = QtWidgets.QPlainTextEdit()
		self.log_view.setReadOnly(True)
		self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

		layout.addWidget(task_label)
		layout.addWidget(self.task_input)
		layout.addLayout(controls_layout)
		layout.addWidget(log_label)
		layout.addWidget(self.log_view, stretch=1)

		self.setCentralWidget(central)

		self.status_bar = QtWidgets.QStatusBar()
		self.setStatusBar(self.status_bar)
		self.progress_bar = QtWidgets.QProgressBar()
		self.progress_bar.setMaximumWidth(220)
		self.progress_bar.setVisible(False)
		self.status_bar.addPermanentWidget(self.progress_bar)

		self.run_button.clicked.connect(self._start_execution)
		self.stop_button.clicked.connect(self._stop_execution)
		self.clear_button.clicked.connect(self.log_view.clear)

	def _setup_menu(self) -> None:
		menu_bar = self.menuBar()

		file_menu = menu_bar.addMenu('ファイル')
		exit_action = QtGui.QAction('終了', self)
		exit_action.setShortcut(QtGui.QKeySequence.Quit)
		exit_action.triggered.connect(self.close)
		file_menu.addAction(exit_action)

		help_menu = menu_bar.addMenu('ヘルプ')
		about_action = QtGui.QAction('このアプリについて', self)
		about_action.triggered.connect(self._show_about_dialog)
		help_menu.addAction(about_action)

	def _show_about_dialog(self) -> None:
		QtWidgets.QMessageBox.information(
			self,
			'Browser Use GUI',
			'PySide6で構築された公式GUIです。\n\n'
			'タスクを入力して「実行」を押すと、エージェントがブラウザ操作を開始します。',
		)

	def _append_log(self, message: str) -> None:
		self.log_view.appendPlainText(message)
		cursor = self.log_view.textCursor()
		cursor.movePosition(QtGui.QTextCursor.End)
		self.log_view.setTextCursor(cursor)

	def _start_execution(self) -> None:
		task = self.task_input.toPlainText().strip()

		if not task:
			QtWidgets.QMessageBox.warning(self, 'エラー', 'タスク内容を入力してください。')
			return

		preferences = UserPreferences(task=task)
		self._worker = AgentWorker(preferences)
		self._bind_worker_signals(self._worker)

		root_logger = logging.getLogger()
		if not any(handler is self._log_handler for handler in root_logger.handlers):
			root_logger.addHandler(self._log_handler)
			self._handler_attached = True
		root_logger.setLevel(logging.INFO)

		logging.getLogger(__name__).info('タスク実行を開始します。')
		self._update_controls(running=True)
		self._worker.start()

	def _bind_worker_signals(self, worker: AgentWorker) -> None:
		worker.status.connect(self._update_status)
		worker.progress.connect(self._update_progress)
		worker.finished.connect(self._on_finished)

	def _stop_execution(self) -> None:
		if self._worker is None:
			return

		self._worker.request_cancel()
		self._update_status('停止要求を送信しました…')

	def _update_status(self, message: str) -> None:
		self.statusBar().showMessage(message)

	def _update_progress(self, current: int, total: int) -> None:
		self.progress_bar.setVisible(True)
		if total > 0:
			self.progress_bar.setMaximum(total)
			self.progress_bar.setValue(min(current, total))
			percentage = int((current / total) * 100)
			self.statusBar().showMessage(f'ステップ {current}/{total} ({percentage}%)')
		else:
			self.progress_bar.setMaximum(1)
			self.progress_bar.setValue(0)
			self.statusBar().showMessage(f'ステップ {current}')

	def _on_finished(self, success: bool, message: str) -> None:
		root_logger = logging.getLogger()
		if self._handler_attached and self._log_handler in root_logger.handlers:
			root_logger.removeHandler(self._log_handler)
			self._handler_attached = False
		self.progress_bar.setVisible(False)
		self._update_controls(running=False)
		self._worker = None

		if success:
			self._update_status(message)
			QtWidgets.QMessageBox.information(self, '完了', message)
			return

		self._update_status(message)
		if 'キャンセル' in message:
			QtWidgets.QMessageBox.warning(self, 'キャンセル', message)
		else:
			QtWidgets.QMessageBox.critical(self, 'エラー', message)

		if self._closing_after_stop:
			self._closing_after_stop = False
			QtCore.QTimer.singleShot(0, self.close)

	def _update_controls(self, running: bool) -> None:
		self.run_button.setEnabled(not running)
		self.stop_button.setEnabled(running)
		self.task_input.setReadOnly(running)

	def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: D401
		if self._worker is not None and self._worker.isRunning():
			reply = QtWidgets.QMessageBox.question(
				self,
				'確認',
				'タスクが実行中です。停止して終了しますか？',
				QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
				QtWidgets.QMessageBox.StandardButton.No,
				)

			if reply == QtWidgets.QMessageBox.StandardButton.Yes:
				self._stop_execution()
				self._closing_after_stop = True
				self.run_button.setEnabled(False)
				self.stop_button.setEnabled(False)
				self.task_input.setReadOnly(True)
				self._update_status('停止処理中...')
				event.ignore()
				return

			event.ignore()
			return

		event.accept()


def main() -> None:
	app = QtWidgets.QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec())


if __name__ == '__main__':
	main()
