from __future__ import annotations

import base64
from typing import Any, Optional

from PySide6 import QtCore, QtGui, QtWidgets


class ApprovalDialog(QtWidgets.QDialog):
	"""Modal dialog prompting the user to approve, retry, skip, or cancel agent actions."""

	def __init__(self, payload: dict[str, Any], parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)
		self.setWindowTitle('ステップ承認が必要です')
		self.setModal(True)
		self.resize(680, 640)

		self._payload = payload
		self._decision: str = 'cancel'
		self._feedback: Optional[str] = None

		self._image_label = QtWidgets.QLabel(alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
		self._image_label.setMinimumHeight(240)
		self._image_label.setObjectName('approval-screenshot')

		self._info_label = QtWidgets.QLabel()
		self._info_label.setWordWrap(True)

		self._thinking_toggle = QtWidgets.QToolButton()
		self._thinking_toggle.setText('Thinking を表示')
		self._thinking_toggle.setCheckable(True)
		self._thinking_toggle.setChecked(False)
		self._thinking_toggle.toggled.connect(self._toggle_thinking)

		self._thinking_label = QtWidgets.QLabel()
		self._thinking_label.setWordWrap(True)
		self._thinking_label.setStyleSheet('color: #555555;')
		self._thinking_label.setVisible(False)

		self._actions_list = QtWidgets.QListWidget()
		self._actions_list.setAlternatingRowColors(True)
		self._actions_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)

		self._feedback_section = QtWidgets.QWidget()
		self._feedback_section.setVisible(False)
		feedback_layout = QtWidgets.QVBoxLayout(self._feedback_section)
		feedback_label = QtWidgets.QLabel('再考のためのフィードバック（任意）')
		self._feedback_edit = QtWidgets.QPlainTextEdit()
		self._feedback_edit.setPlaceholderText('例: このボタンは押さないでください。代わりに○○をクリックしてください。')
		self._feedback_edit.setMinimumHeight(90)
		feedback_layout.addWidget(feedback_label)
		feedback_layout.addWidget(self._feedback_edit)

		layout = QtWidgets.QVBoxLayout(self)
		layout.addWidget(self._image_label)
		layout.addWidget(self._info_label)
		layout.addWidget(self._thinking_toggle)
		layout.addWidget(self._thinking_label)
		layout.addWidget(QtWidgets.QLabel('実行予定のアクション:', parent=self))
		layout.addWidget(self._actions_list)
		layout.addWidget(self._feedback_section)

		button_row = QtWidgets.QHBoxLayout()
		button_row.addStretch(1)
		self._approve_button = QtWidgets.QPushButton('承認')
		self._retry_button = QtWidgets.QPushButton('再考')
		self._skip_button = QtWidgets.QPushButton('スキップ')
		self._cancel_button = QtWidgets.QPushButton('中止')
		button_row.addWidget(self._approve_button)
		button_row.addWidget(self._retry_button)
		button_row.addWidget(self._skip_button)
		button_row.addWidget(self._cancel_button)
		layout.addLayout(button_row)

		self._approve_button.clicked.connect(self._on_approve)
		self._retry_button.clicked.connect(self._on_retry)
		self._skip_button.clicked.connect(self._on_skip)
		self._cancel_button.clicked.connect(self._on_cancel)

		self._populate()

	@property
	def decision(self) -> str:
		return self._decision

	@property
	def feedback(self) -> Optional[str]:
		return self._feedback

	def _populate(self) -> None:
		self._populate_screenshot(self._payload.get('screenshot'))

		step_number = self._payload.get('step_number')
		max_steps = self._payload.get('max_steps')
		url = self._payload.get('url') or '—'
		title = self._payload.get('title') or '—'
		next_goal = self._payload.get('next_goal') or '—'

		info_parts: list[str] = []
		if step_number is not None and max_steps:
			info_parts.append(f'ステップ {step_number} / {max_steps}')
		info_parts.append(f'ページタイトル: {title}')
		info_parts.append(f'URL: {url}')
		info_parts.append(f'次のゴール: {next_goal}')

		self._info_label.setText('\n'.join(info_parts))

		thinking = self._payload.get('thinking')
		if thinking:
			self._thinking_label.setText(thinking)
			self._thinking_toggle.setEnabled(True)
			self._thinking_toggle.setText('Thinking を表示')
		else:
			self._thinking_label.setText('')
			self._thinking_label.setVisible(False)
			self._thinking_toggle.setEnabled(False)
			self._thinking_toggle.setText('Thinking はありません')

		actions = self._payload.get('actions') or []
		if actions:
			for action in actions:
				self._actions_list.addItem(str(action))
		else:
			self._actions_list.addItem('（アクションが提案されていません）')

	def _populate_screenshot(self, encoded: str | None) -> None:
		if not encoded:
			self._image_label.setText('スクリーンショットがありません')
			self._image_label.setPixmap(QtGui.QPixmap())
			return

		try:
			image_bytes = base64.b64decode(encoded)
		except (ValueError, TypeError):
			self._image_label.setText('スクリーンショットを表示できません')
			self._image_label.setPixmap(QtGui.QPixmap())
			return

		pixmap = QtGui.QPixmap()
		if not pixmap.loadFromData(image_bytes):
			self._image_label.setText('スクリーンショットを表示できません')
			self._image_label.setPixmap(QtGui.QPixmap())
			return

		max_width = 520
		if pixmap.width() > max_width:
			pixmap = pixmap.scaledToWidth(max_width, QtCore.Qt.TransformationMode.SmoothTransformation)
		self._image_label.setPixmap(pixmap)
		self._image_label.setText('')

	def _on_approve(self) -> None:
		self._decision = 'approve'
		self._feedback = None
		self.accept()

	def _on_retry(self) -> None:
		if not self._feedback_section.isVisible():
			self._feedback_section.setVisible(True)
			self._retry_button.setText('再考（送信）')
			self._feedback_edit.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
			return

		self._decision = 'retry'
		feedback = self._feedback_edit.toPlainText().strip()
		self._feedback = feedback or None
		self.accept()

	def _on_skip(self) -> None:
		self._decision = 'skip'
		self._feedback = None
		self.accept()

	def _on_cancel(self) -> None:
		self._decision = 'cancel'
		self._feedback = None
		self.reject()

	def reject(self) -> None:  # noqa: D401
		"""Ensure closing the dialog is treated as cancel."""
		self._decision = 'cancel'
		self._feedback = None
		super().reject()

	def _toggle_thinking(self, checked: bool) -> None:
		if not self._thinking_label.text():
			return
		self._thinking_label.setVisible(checked)
		self._thinking_toggle.setText('Thinking を隠す' if checked else 'Thinking を表示')
