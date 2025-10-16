from __future__ import annotations

from PySide6 import QtGui, QtWidgets


class LogTabsPanel(QtWidgets.QGroupBox):
	"""Tabbed view for log messages."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__('ログ', parent)

		self.tabs = QtWidgets.QTabWidget()
		self.main_log = self._create_editor()
		self.event_log = self._create_editor()
		self.cdp_log = self._create_editor()

		self.tabs.addTab(self.main_log, 'メイン')
		self.tabs.addTab(self.event_log, 'イベント')
		self.tabs.addTab(self.cdp_log, 'CDP')

		layout = QtWidgets.QVBoxLayout(self)
		layout.addWidget(self.tabs)

	def _create_editor(self) -> QtWidgets.QTextEdit:
		editor = QtWidgets.QTextEdit()
		editor.setReadOnly(True)
		editor.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
		return editor

	def append_message(self, message: str) -> None:
		target = self.main_log
		log_upper = message.upper()
		if '[EVENT]' in log_upper:
			target = self.event_log
		elif '[CDP]' in log_upper:
			target = self.cdp_log

		target.append(message)
		cursor = target.textCursor()
		cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
		target.setTextCursor(cursor)

	def clear_all(self) -> None:
		for editor in (self.main_log, self.event_log, self.cdp_log):
			editor.clear()
