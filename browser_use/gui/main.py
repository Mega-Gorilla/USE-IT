from __future__ import annotations

import logging
import sys
from typing import List

from PySide6 import QtCore, QtGui, QtWidgets
from dotenv import load_dotenv

from browser_use.gui.worker import AgentWorker, QtLogHandler, UserPreferences
from browser_use.gui.widgets import (
	BrowserInfoPanel,
	LogTabsPanel,
	ModelInfoPanel,
	StepInfoPanel,
	TaskHistoryEntry,
	TaskHistoryList,
	TaskInputPanel,
)

load_dotenv()


class MainWindow(QtWidgets.QMainWindow):
	def __init__(self) -> None:
		super().__init__()
		self.setWindowTitle('Browser Use GUI')
		self.resize(960, 720)

		self._worker: AgentWorker | None = None
		self._log_handler = QtLogHandler()
		self._log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
		self._log_handler.signal.connect(self._on_log_message)
		self._handler_attached = False
		self._closing_after_stop = False
		self._history_entries: List[TaskHistoryEntry] = []
		self._current_history_index: int | None = None

		self._init_ui()
		self._connect_signals()
		self._update_controls(running=False)
		self._load_initial_panels()

	def _init_ui(self) -> None:
		central = QtWidgets.QWidget()
		self.setCentralWidget(central)
		main_layout = QtWidgets.QVBoxLayout(central)

		# Task input area
		self.task_panel = TaskInputPanel()
		main_layout.addWidget(self.task_panel)

		# Split view for history and detail panes
		splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

		# Left: history
		left_panel = QtWidgets.QWidget()
		left_layout = QtWidgets.QVBoxLayout(left_panel)
		self.history_panel = TaskHistoryList()
		left_layout.addWidget(self.history_panel)
		left_layout.addStretch(1)
		splitter.addWidget(left_panel)

		# Right: info + logs
		right_panel = QtWidgets.QWidget()
		right_layout = QtWidgets.QVBoxLayout(right_panel)

		info_layout = QtWidgets.QHBoxLayout()
		self.browser_panel = BrowserInfoPanel()
		self.model_panel = ModelInfoPanel()
		self.step_panel = StepInfoPanel()
		info_layout.addWidget(self.browser_panel)
		info_layout.addWidget(self.model_panel)
		info_layout.addWidget(self.step_panel)

		right_layout.addLayout(info_layout)

		self.log_panel = LogTabsPanel()
		right_layout.addWidget(self.log_panel, stretch=1)

		splitter.addWidget(right_panel)
		splitter.setStretchFactor(0, 1)
		splitter.setStretchFactor(1, 3)

		main_layout.addWidget(splitter, stretch=1)

		# Status bar
		self.status_bar = QtWidgets.QStatusBar()
		self.setStatusBar(self.status_bar)
		self.progress_bar = QtWidgets.QProgressBar()
		self.progress_bar.setMaximumWidth(220)
		self.progress_bar.setVisible(False)
		self.status_bar.addPermanentWidget(self.progress_bar)

		self._setup_menu()

	def _setup_menu(self) -> None:
		menu_bar = self.menuBar()

		file_menu = menu_bar.addMenu('ファイル')
		exit_action = QtGui.QAction('終了', self)
		exit_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Quit))
		exit_action.triggered.connect(self.close)
		file_menu.addAction(exit_action)

		help_menu = menu_bar.addMenu('ヘルプ')
		about_action = QtGui.QAction('このアプリについて', self)
		about_action.triggered.connect(self._show_about_dialog)
		help_menu.addAction(about_action)

	def _connect_signals(self) -> None:
		self.task_panel.connect_run(self._start_execution)
		self.task_panel.connect_stop(self._stop_execution)
		self.task_panel.connect_clear(self._clear_logs)
		self.history_panel.list_widget.itemSelectionChanged.connect(self._on_history_selection_changed)

	def _show_about_dialog(self) -> None:
		QtWidgets.QMessageBox.information(
			self,
			'Browser Use GUI',
			'PySide6で構築された公式GUIです。\n\n'
			'タスクを入力して「実行」を押すと、エージェントがブラウザ操作を開始します。',
		)

	def _on_log_message(self, message: str) -> None:
		self.log_panel.append_message(message)

	def _start_execution(self) -> None:
		task = self.task_panel.task_text().strip()
		if not task:
			QtWidgets.QMessageBox.warning(self, 'エラー', 'タスク内容を入力してください。')
			return

		preferences = UserPreferences(task=task)
		self._worker = AgentWorker(preferences)
		self._update_info_panels_from_worker(self._worker)
		self._bind_worker_signals(self._worker)
		self._add_history_entry(task)
		self.browser_panel.set_status('実行中')
		self._closing_after_stop = False
		self.step_panel.clear()

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
		worker.step_update.connect(self.step_panel.update_snapshot)

	def _stop_execution(self) -> None:
		if self._worker is None:
			return

		self._worker.request_cancel()
		self.browser_panel.set_status('停止要求中')
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
		self.browser_panel.set_status('未接続')
		self._finalize_history_entry(success, message)

		self._update_status(message)
		if success:
			QtWidgets.QMessageBox.information(self, '完了', message)
		else:
			if 'キャンセル' in message:
				QtWidgets.QMessageBox.warning(self, 'キャンセル', message)
			else:
				QtWidgets.QMessageBox.critical(self, 'エラー', message)

		if self._closing_after_stop:
			self._closing_after_stop = False
			QtCore.QTimer.singleShot(0, self.close)

	def _update_controls(self, running: bool) -> None:
		self.task_panel.set_running_state(running)

	def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
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
				self.task_panel.set_running_state(True)
				self._update_status('停止処理中...')
				event.ignore()
				return

			event.ignore()
			return

		event.accept()

	def _clear_logs(self) -> None:
		self.log_panel.clear_all()

	def _add_history_entry(self, task: str) -> None:
		entry = TaskHistoryEntry(task=task, status='実行中')
		self._history_entries.insert(0, entry)
		self._current_history_index = 0
		self._refresh_history_list(select_index=0)

	def _refresh_history_list(self, select_index: int | None = None) -> None:
		self.history_panel.set_entries(self._history_entries, select_index=select_index)

	def _finalize_history_entry(self, success: bool, message: str) -> None:
		if self._current_history_index is None:
			return
		try:
			entry = self._history_entries[self._current_history_index]
		except IndexError:
			return

		entry.finished_at = QtCore.QDateTime.currentDateTime()
		entry.result_summary = message
		if success:
			entry.status = '完了'
		elif 'キャンセル' in message:
			entry.status = 'キャンセル'
		else:
			entry.status = '失敗'

		self._refresh_history_list(select_index=self._current_history_index)
		if self._current_history_index is not None:
			self.history_panel.set_current_row(self._current_history_index)

	def _on_history_selection_changed(self) -> None:
		if self._worker is not None and self._worker.isRunning():
			return

		row = self.history_panel.current_row()
		if row < 0 or row >= len(self._history_entries):
			return
		entry = self._history_entries[row]
		self._current_history_index = row
		summary = entry.result_summary or entry.status
		self._update_status(f'[{entry.status}] {summary}')

	def _update_info_panels_from_worker(self, worker: AgentWorker) -> None:
		llm_summary = worker.llm_summary
		browser_summary = worker.browser_summary

		self.model_panel.update_summary(llm_summary)
		self.browser_panel.update_summary(browser_summary)

	def _load_initial_panels(self) -> None:
		try:
			worker = AgentWorker(UserPreferences(task=''))
			self._update_info_panels_from_worker(worker)
		except Exception as exc:
			logging.getLogger(__name__).debug(f'初期情報パネルの読み込みに失敗しました: {exc}')


def main() -> None:
	app = QtWidgets.QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec())


if __name__ == '__main__':
	main()
