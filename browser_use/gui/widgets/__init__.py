"""Reusable widget components for the Browser Use GUI."""

from .history_list import TaskHistoryEntry, TaskHistoryList
from .info_panels import BrowserInfoPanel, ModelInfoPanel
from .log_tabs import LogTabsPanel
from .step_info import StepInfoPanel
from .task_input import TaskInputPanel

__all__ = [
	'TaskHistoryList',
	'BrowserInfoPanel',
	'ModelInfoPanel',
	'LogTabsPanel',
	'StepInfoPanel',
	'TaskInputPanel',
	'TaskHistoryEntry',
]
