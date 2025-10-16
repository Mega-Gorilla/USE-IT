from __future__ import annotations

from typing import Callable

from PySide6 import QtWidgets


class TaskInputPanel(QtWidgets.QGroupBox):
	"""Task input area with controls for execution."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__('タスク入力', parent)

		self.task_input = QtWidgets.QPlainTextEdit()
		self.task_input.setPlaceholderText('例: Amazonで最新のノイズキャンセリングヘッドフォンを探してレポートしてください')

		self.run_button = QtWidgets.QPushButton('実行')
		self.stop_button = QtWidgets.QPushButton('停止')
		self.clear_button = QtWidgets.QPushButton('ログをクリア')

		self._build_layout()

	def _build_layout(self) -> None:
		layout = QtWidgets.QVBoxLayout(self)
		layout.addWidget(self.task_input)

		buttons = QtWidgets.QHBoxLayout()
		buttons.addWidget(self.run_button)
		buttons.addWidget(self.stop_button)
		buttons.addWidget(self.clear_button)
		buttons.addStretch(1)

		layout.addLayout(buttons)

	def task_text(self) -> str:
		return self.task_input.toPlainText()

	def set_running_state(self, running: bool) -> None:
		self.run_button.setEnabled(not running)
		self.stop_button.setEnabled(running)
		self.task_input.setReadOnly(running)

	def clear(self) -> None:
		self.task_input.clear()

	def connect_run(self, slot: Callable[..., None]) -> None:
		self.run_button.clicked.connect(slot)  # type: ignore[arg-type]

	def connect_stop(self, slot: Callable[..., None]) -> None:
		self.stop_button.clicked.connect(slot)  # type: ignore[arg-type]

	def connect_clear(self, slot: Callable[..., None]) -> None:
		self.clear_button.clicked.connect(slot)  # type: ignore[arg-type]
