"""History tab for reviewing past task executions."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .history_list import TaskHistoryEntry, TaskHistoryList
from .info_panels import BrowserInfoPanel, ModelInfoPanel


class TaskDetailPanel(QtWidgets.QWidget):
	"""Display detailed information about selected task."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)

		layout = QtWidgets.QVBoxLayout(self)

		# タスク詳細エリア
		detail_group = QtWidgets.QGroupBox('📋 タスク詳細')
		detail_layout = QtWidgets.QFormLayout(detail_group)

		self.task_label = QtWidgets.QLabel('—')
		self.task_label.setWordWrap(True)
		self.task_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

		self.status_label = QtWidgets.QLabel('—')
		self.result_label = QtWidgets.QLabel('—')
		self.result_label.setWordWrap(True)

		self.started_label = QtWidgets.QLabel('—')
		self.finished_label = QtWidgets.QLabel('—')
		self.duration_label = QtWidgets.QLabel('—')

		detail_layout.addRow('タスク:', self.task_label)
		detail_layout.addRow('ステータス:', self.status_label)
		detail_layout.addRow('結果:', self.result_label)
		detail_layout.addRow('開始:', self.started_label)
		detail_layout.addRow('終了:', self.finished_label)
		detail_layout.addRow('実行時間:', self.duration_label)

		layout.addWidget(detail_group)
		layout.addStretch()

	def update_entry(self, entry: TaskHistoryEntry) -> None:
		"""Update display with task entry details."""
		self.task_label.setText(entry.task)

		# ステータスに応じたアイコン
		status_icons = {'完了': '✅', '失敗': '❌', 'キャンセル': '⏸', '実行中': '⚡'}
		icon = status_icons.get(entry.status, '📌')
		self.status_label.setText(f'{icon} {entry.status}')

		self.result_label.setText(entry.result_summary or '—')
		self.started_label.setText(entry.started_at.toString('yyyy-MM-dd HH:mm:ss'))

		if entry.finished_at:
			self.finished_label.setText(entry.finished_at.toString('yyyy-MM-dd HH:mm:ss'))
			# 実行時間を計算
			duration_secs = entry.started_at.secsTo(entry.finished_at)
			if duration_secs >= 60:
				minutes = duration_secs // 60
				seconds = duration_secs % 60
				self.duration_label.setText(f'{minutes}分{seconds}秒')
			else:
				self.duration_label.setText(f'{duration_secs}秒')
		else:
			self.finished_label.setText('—')
			self.duration_label.setText('—')

	def clear(self) -> None:
		"""Clear all detail fields."""
		self.task_label.setText('—')
		self.status_label.setText('—')
		self.result_label.setText('—')
		self.started_label.setText('—')
		self.finished_label.setText('—')
		self.duration_label.setText('—')


class HistoryTab(QtWidgets.QWidget):
	"""Tab for displaying task history and details."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)

		layout = QtWidgets.QHBoxLayout(self)

		# 左側: タスク一覧
		self.history_list = TaskHistoryList()
		layout.addWidget(self.history_list, stretch=1)

		# 右側: 詳細表示エリア
		right_panel = QtWidgets.QWidget()
		right_layout = QtWidgets.QVBoxLayout(right_panel)

		# タスク詳細
		self.detail_panel = TaskDetailPanel()
		right_layout.addWidget(self.detail_panel)

		# 設定情報（折りたたみ可能）
		config_group = QtWidgets.QGroupBox('🔧 設定情報')
		config_group.setCheckable(True)
		config_group.setChecked(False)
		config_layout = QtWidgets.QHBoxLayout(config_group)
		self.browser_panel = BrowserInfoPanel()
		self.model_panel = ModelInfoPanel()
		config_layout.addWidget(self.browser_panel)
		config_layout.addWidget(self.model_panel)
		right_layout.addWidget(config_group)

		right_layout.addStretch()
		layout.addWidget(right_panel, stretch=2)
