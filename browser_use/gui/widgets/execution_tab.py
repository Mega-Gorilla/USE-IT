"""Execution tab combining step information and logs."""

from __future__ import annotations

from PySide6 import QtWidgets

from .log_tabs import LogTabsPanel
from .step_info import StepInfoPanel


class ExecutionTab(QtWidgets.QWidget):
	"""Tab for displaying current execution status."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)

		layout = QtWidgets.QVBoxLayout(self)
		layout.setContentsMargins(5, 5, 5, 5)

		# ステップ情報（現在のゴール、アクション、状態）
		self.step_panel = StepInfoPanel()
		layout.addWidget(self.step_panel, stretch=1)

		# 実行ログ（メイン/イベント/CDP）
		self.log_panel = LogTabsPanel()
		layout.addWidget(self.log_panel, stretch=1)
