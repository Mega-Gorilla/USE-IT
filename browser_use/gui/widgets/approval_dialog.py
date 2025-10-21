from __future__ import annotations

import base64
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets


class ApprovalDialog(QtWidgets.QDialog):
	"""Modal dialog prompting the user to approve or cancel the next agent actions."""

	def __init__(self, payload: dict[str, Any], parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)
		self.setWindowTitle('ステップ承認が必要です')
		self.setModal(True)
		self.resize(640, 600)
		self._decision: str = 'cancel'
		self._payload = payload

		self._image_label = QtWidgets.QLabel(alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
		self._image_label.setMinimumHeight(240)
		self._image_label.setObjectName('approval-screenshot')
		self._info_label = QtWidgets.QLabel()
		self._info_label.setWordWrap(True)

		self._actions_list = QtWidgets.QListWidget()
		self._actions_list.setAlternatingRowColors(True)
		self._actions_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)

		layout = QtWidgets.QVBoxLayout(self)
		layout.addWidget(self._image_label)

		layout.addWidget(self._info_label)
		layout.addWidget(QtWidgets.QLabel('実行予定のアクション:', parent=self))
		layout.addWidget(self._actions_list)

		self._button_box = QtWidgets.QDialogButtonBox(parent=self)
		self._approve_button = self._button_box.addButton('承認', QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
		self._cancel_button = self._button_box.addButton('中止', QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)

		self._approve_button.clicked.connect(self._on_approve)
		self._cancel_button.clicked.connect(self._on_cancel)

		layout.addWidget(self._button_box)
		self._populate()

	@property
	def decision(self) -> str:
		return self._decision

	def _populate(self) -> None:
		self._populate_screenshot(self._payload.get('screenshot'))
		step_number = self._payload.get('step_number')
		max_steps = self._payload.get('max_steps')
		url = self._payload.get('url') or '—'
		title = self._payload.get('title') or '—'
		next_goal = self._payload.get('next_goal') or '—'
		info_parts = []
		if step_number is not None and max_steps:
			info_parts.append(f'ステップ {step_number} / {max_steps}')
		info_parts.append(f'ページタイトル: {title}')
		info_parts.append(f'URL: {url}')
		info_parts.append(f'次のゴール: {next_goal}')
		thinking = self._payload.get('thinking')
		if thinking:
			info_parts.append(f'Thinking: {thinking}')
		self._info_label.setText('\n'.join(info_parts))

		actions = self._payload.get('actions') or []
		if actions:
			for action in actions:
				self._actions_list.addItem(str(action))
		else:
			self._actions_list.addItem('（アクションが提案されていません）')

	def _populate_screenshot(self, encoded: str | None) -> None:
		if not encoded:
			self._image_label.setText('スクリーンショットがありません')
			return

		try:
			image_bytes = base64.b64decode(encoded)
		except (ValueError, TypeError):
			self._image_label.setText('スクリーンショットを表示できません')
			return

		pixmap = QtGui.QPixmap()
		if not pixmap.loadFromData(image_bytes):
			self._image_label.setText('スクリーンショットを表示できません')
			return

		max_width = 480
		if pixmap.width() > max_width:
			pixmap = pixmap.scaledToWidth(max_width, QtCore.Qt.TransformationMode.SmoothTransformation)
		self._image_label.setPixmap(pixmap)

	def _on_approve(self) -> None:
		self._decision = 'approve'
		self.accept()

	def _on_cancel(self) -> None:
		self._decision = 'cancel'
		self.reject()

	def reject(self) -> None:  # noqa: D401
		"""Ensure closing the dialog is treated as cancel."""
		self._decision = 'cancel'
		super().reject()
