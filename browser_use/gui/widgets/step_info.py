from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets


class StepInfoPanel(QtWidgets.QGroupBox):
	"""Render step-level metadata coming from the agent."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__('ステップ情報', parent)

		self._snapshot: dict[str, Any] | None = None

		self.progress_label = QtWidgets.QLabel('—')
		self.url_label = QtWidgets.QLabel('—')
		self.url_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
		self.url_label.setWordWrap(True)
		self.title_label = QtWidgets.QLabel('—')
		self.title_label.setWordWrap(True)

		self.thinking_edit = self._create_text_edit()
		self.memory_edit = self._create_text_edit()
		self.next_goal_edit = self._create_text_edit()
		self.evaluation_edit = self._create_text_edit()
		self.actions_edit = self._create_text_edit(max_height=120, min_height=70)

		form = QtWidgets.QFormLayout(self)
		form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)
		form.addRow('進捗', self.progress_label)
		form.addRow('URL', self.url_label)
		form.addRow('タイトル', self.title_label)
		form.addRow('Thinking', self.thinking_edit)
		form.addRow('Memory', self.memory_edit)
		form.addRow('Next Goal', self.next_goal_edit)
		form.addRow('前ゴール評価', self.evaluation_edit)
		form.addRow('Actions', self.actions_edit)

	def _create_text_edit(self, *, max_height: int = 80, min_height: int = 50) -> QtWidgets.QPlainTextEdit:
		edit = QtWidgets.QPlainTextEdit()
		edit.setReadOnly(True)
		edit.setUndoRedoEnabled(False)
		edit.setMaximumBlockCount(200)
		edit.setPlaceholderText('—')
		edit.setMaximumHeight(max_height)
		edit.setMinimumHeight(min_height)
		edit.setWordWrapMode(QtGui.QTextOption.WrapMode.WordWrap)
		return edit

	def clear(self) -> None:
		self._snapshot = None
		self.progress_label.setText('—')
		self.url_label.setText('—')
		self.title_label.setText('—')
		for edit in (
			self.thinking_edit,
			self.memory_edit,
			self.next_goal_edit,
			self.evaluation_edit,
			self.actions_edit,
		):
			edit.clear()

	def update_snapshot(self, snapshot: dict[str, Any]) -> None:
		self._snapshot = snapshot
		step_number = snapshot.get('step_number')
		max_steps = snapshot.get('max_steps')

		if step_number is not None and max_steps:
			self.progress_label.setText(f'{step_number}/{max_steps}')
		elif step_number is not None:
			self.progress_label.setText(str(step_number))
		else:
			self.progress_label.setText('—')

		url = snapshot.get('url') or ''
		self.url_label.setText(url if url else '—')

		title = snapshot.get('title') or ''
		self.title_label.setText(title if title else '—')

		self.thinking_edit.setPlainText(snapshot.get('thinking') or '')
		self.memory_edit.setPlainText(snapshot.get('memory') or '')
		self.next_goal_edit.setPlainText(snapshot.get('next_goal') or '')
		self.evaluation_edit.setPlainText(snapshot.get('evaluation_previous_goal') or '')

		actions = snapshot.get('actions') or []
		self.actions_edit.setPlainText('\n'.join(actions))

	@property
	def snapshot(self) -> dict[str, Any] | None:
		return self._snapshot
