from __future__ import annotations

from typing import Any

from PySide6 import QtWidgets


class BrowserInfoPanel(QtWidgets.QGroupBox):
	"""Display and manage browser related status."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__('ブラウザ情報', parent)

		self.status_label = QtWidgets.QLabel('未接続')
		self.mode_label = QtWidgets.QLabel('表示')
		self.keep_alive_label = QtWidgets.QLabel('有効')
		self.proxy_label = QtWidgets.QLabel('なし')

		form = QtWidgets.QFormLayout(self)
		form.addRow('ステータス', self.status_label)
		form.addRow('モード', self.mode_label)
		form.addRow('Keep-Alive', self.keep_alive_label)
		form.addRow('プロキシ', self.proxy_label)

	def set_status(self, status: str) -> None:
		self.status_label.setText(status)

	def update_summary(self, summary: dict[str, Any]) -> None:
		headless = bool(summary.get('headless', False))
		self.mode_label.setText('ヘッドレス' if headless else '表示')
		self.keep_alive_label.setText('有効' if summary.get('keep_alive', True) else '無効')

		proxy = summary.get('proxy')
		self.proxy_label.setText(str(proxy) if proxy else 'なし')

	def reset(self) -> None:
		self.set_status('未接続')
		self.mode_label.setText('表示')
		self.keep_alive_label.setText('有効')
		self.proxy_label.setText('なし')


class ModelInfoPanel(QtWidgets.QGroupBox):
	"""Display information about the active model."""

	def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__('モデル情報', parent)

		self.model_label = QtWidgets.QLabel('—')
		self.temperature_label = QtWidgets.QLabel('—')
		self.thinking_label = QtWidgets.QLabel('—')

		form = QtWidgets.QFormLayout(self)
		form.addRow('モデル', self.model_label)
		form.addRow('Temperature', self.temperature_label)
		form.addRow('Thinking', self.thinking_label)

	def update_summary(self, summary: dict[str, Any]) -> None:
		self.model_label.setText(str(summary.get('model', '—')))
		self.temperature_label.setText(str(summary.get('temperature', '—')))
		self.thinking_label.setText('有効' if summary.get('use_thinking') else '無効')

	def reset(self) -> None:
		self.model_label.setText('—')
		self.temperature_label.setText('—')
		self.thinking_label.setText('—')
