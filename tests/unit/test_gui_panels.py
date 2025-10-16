from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from browser_use.gui.widgets import HistoryTab, StepInfoPanel, TaskHistoryEntry


def _ensure_qapp() -> QtWidgets.QApplication:
	app = QtWidgets.QApplication.instance()
	if app is None:
		app = QtWidgets.QApplication([])
	return app


def test_step_info_panel_updates_snapshot() -> None:
	_ensure_qapp()
	panel = StepInfoPanel()

	snapshot = {
		'step_number': 2,
		'max_steps': 5,
		'url': 'https://example.com',
		'title': 'Example',
		'thinking': '考え中',
		'memory': '覚えていること',
		'evaluation_previous_goal': '評価OK',
		'next_goal': '検索結果を確認する',
		'actions': ['click(index=1)', 'type(text="hello")'],
	}

	panel.update_snapshot(snapshot)

	assert panel.next_goal_label.text() == '検索結果を確認する'
	assert panel.actions_list.count() == 2
	assert panel.step_label.text() == '2/5'
	assert panel.url_label.text() == 'https://example.com'
	assert panel.title_label.text() == 'Example'
	assert panel.thinking_edit.toPlainText() == '考え中'
	assert panel.memory_edit.toPlainText() == '覚えていること'
	assert panel.evaluation_edit.toPlainText() == '評価OK'


def test_history_tab_detail_panel_renders_entry() -> None:
	_ensure_qapp()
	tab = HistoryTab()

	start = QtCore.QDateTime.currentDateTime()
	finished = start.addSecs(125)
	entry = TaskHistoryEntry(
		task='GitHubでissue #1を調査する',
		status='完了',
		started_at=start,
		finished_at=finished,
		result_summary='調査完了、修正不要でした。',
	)

	tab.detail_panel.update_entry(entry)

	assert '✅ 完了' in tab.detail_panel.status_label.text()
	assert '調査完了、修正不要でした。' == tab.detail_panel.result_label.text()
	assert tab.detail_panel.finished_label.text() != '—'
	assert tab.detail_panel.duration_label.text() == '2分5秒'
