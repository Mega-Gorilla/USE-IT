from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets


class StepInfoPanel(QtWidgets.QWidget):
	"""Render step-level information with clear hierarchy."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)
		self._snapshot: dict[str, Any] | None = None

		layout = QtWidgets.QVBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)

		# 1. 最重要: 現在のゴール
		goal_group = QtWidgets.QGroupBox('🎯 ゴール')
		goal_layout = QtWidgets.QVBoxLayout(goal_group)
		self.next_goal_label = QtWidgets.QLabel('タスク実行待機中...')
		self.next_goal_label.setWordWrap(True)
		font = self.next_goal_label.font()
		font.setPointSize(font.pointSize() + 2)
		font.setBold(True)
		self.next_goal_label.setFont(font)
		self.next_goal_label.setStyleSheet('padding: 12px; background-color: #e3f2fd; border-radius: 4px;')
		goal_layout.addWidget(self.next_goal_label)
		layout.addWidget(goal_group)

		# 2. 重要: 実行中のアクション
		actions_group = QtWidgets.QGroupBox('⚡ アクション')
		actions_layout = QtWidgets.QVBoxLayout(actions_group)
		self.actions_list = QtWidgets.QListWidget()
		self.actions_list.setMaximumHeight(100)
		self.actions_list.setStyleSheet('font-family: monospace;')
		actions_layout.addWidget(self.actions_list)
		layout.addWidget(actions_group)

		# 3. 基本情報: ステップ、URL、タイトル
		state_group = QtWidgets.QGroupBox('📍 現在の状態')
		state_layout = QtWidgets.QFormLayout(state_group)

		# プログレスバーウィジェット廃止 → シンプルなステップ表示（文字）
		self.step_label = QtWidgets.QLabel('—')
		self.step_label.setStyleSheet('font-size: 14pt; font-weight: bold;')

		self.url_label = QtWidgets.QLabel('—')
		self.url_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
		self.url_label.setWordWrap(True)

		self.title_label = QtWidgets.QLabel('—')
		self.title_label.setWordWrap(True)

		state_layout.addRow('ステップ:', self.step_label)
		state_layout.addRow('🌐 URL:', self.url_label)
		state_layout.addRow('📄 タイトル:', self.title_label)
		layout.addWidget(state_group)

		# 4. 補助情報: Thinking、Memory、評価（折りたたみ可能）
		self.details_group = QtWidgets.QGroupBox('🔽 詳細情報')
		self.details_group.setCheckable(True)
		self.details_group.setChecked(False)  # デフォルトで折りたたむ
		details_layout = QtWidgets.QFormLayout(self.details_group)

		self.thinking_edit = self._create_text_edit()
		self.memory_edit = self._create_text_edit()
		self.evaluation_edit = self._create_text_edit()

		details_layout.addRow('💭 Thinking:', self.thinking_edit)
		details_layout.addRow('💾 Memory:', self.memory_edit)
		details_layout.addRow('✅ 前ゴール評価:', self.evaluation_edit)
		layout.addWidget(self.details_group)

		layout.addStretch()

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
		self.next_goal_label.setText('タスク実行待機中...')
		self.actions_list.clear()
		self.step_label.setText('—')
		self.url_label.setText('—')
		self.title_label.setText('—')
		self.thinking_edit.clear()
		self.memory_edit.clear()
		self.evaluation_edit.clear()

	def update_snapshot(self, snapshot: dict[str, Any]) -> None:
		self._snapshot = snapshot

		# 1. Next Goal を最優先で更新
		next_goal = snapshot.get('next_goal') or 'ゴール設定中...'
		self.next_goal_label.setText(next_goal)

		# 2. Actions をリスト形式で表示
		self.actions_list.clear()
		actions = snapshot.get('actions') or []
		if actions:
			for action in actions:
				item = QtWidgets.QListWidgetItem(f'• {action}')
				self.actions_list.addItem(item)
		else:
			item = QtWidgets.QListWidgetItem('待機中...')
			item.setForeground(QtGui.QColor('#999'))
			self.actions_list.addItem(item)

		# 3. ステップ番号（プログレスバーウィジェットなし、文字表示のみ）
		step_number = snapshot.get('step_number')
		max_steps = snapshot.get('max_steps')
		if step_number is not None and max_steps:
			self.step_label.setText(f'{step_number}/{max_steps}')
		elif step_number is not None:
			self.step_label.setText(f'{step_number}')
		else:
			self.step_label.setText('—')

		# 4. URL/Title
		url = snapshot.get('url') or ''
		self.url_label.setText(url if url else '—')

		title = snapshot.get('title') or ''
		self.title_label.setText(title if title else '—')

		# 5. 詳細情報
		self.thinking_edit.setPlainText(snapshot.get('thinking') or '')
		self.memory_edit.setPlainText(snapshot.get('memory') or '')
		self.evaluation_edit.setPlainText(snapshot.get('evaluation_previous_goal') or '')

	@property
	def snapshot(self) -> dict[str, Any] | None:
		return self._snapshot
