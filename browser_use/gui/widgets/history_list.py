from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6 import QtCore, QtWidgets


@dataclass(slots=True)
class TaskHistoryEntry:
	task: str
	status: str = '準備中'
	started_at: QtCore.QDateTime = field(default_factory=QtCore.QDateTime.currentDateTime)
	finished_at: QtCore.QDateTime | None = None
	result_summary: str | None = None


class TaskHistoryList(QtWidgets.QGroupBox):
	"""Task history list with utility helpers for formatting."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__('タスク履歴', parent)

		self.list_widget = QtWidgets.QListWidget()
		self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

		layout = QtWidgets.QVBoxLayout(self)
		layout.addWidget(self.list_widget)

	def set_entries(self, entries: Iterable[TaskHistoryEntry], select_index: int | None = None) -> None:
		items: list[str] = []
		for entry in entries:
			start_time = entry.started_at.toString('HH:mm:ss')
			task_text = entry.task
			if len(task_text) > 50:
				task_text = task_text[:47] + '...'
			items.append(f'[{entry.status}] {start_time}  {task_text}')

		self.list_widget.blockSignals(True)
		self.list_widget.clear()
		for item in items:
			self.list_widget.addItem(item)
		self.list_widget.blockSignals(False)

		if select_index is not None and 0 <= select_index < self.list_widget.count():
			self.list_widget.setCurrentRow(select_index)

	def current_row(self) -> int:
		return self.list_widget.currentRow()

	def set_current_row(self, row: int) -> None:
		self.list_widget.setCurrentRow(row)

	def clear(self) -> None:
		self.list_widget.clear()
