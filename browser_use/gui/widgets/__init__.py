"""Reusable widget components for the Browser Use GUI."""

from .approval_dialog import ApprovalDialog
from .execution_tab import ExecutionTab
from .history_list import TaskHistoryEntry, TaskHistoryList
from .history_tab import HistoryTab
from .info_panels import BrowserInfoPanel, ModelInfoPanel
from .log_tabs import LogTabsPanel
from .step_info import StepInfoPanel
from .task_input import TaskInputPanel

__all__ = [
	'ExecutionTab',
	'HistoryTab',
	'TaskHistoryList',
	'BrowserInfoPanel',
	'ModelInfoPanel',
	'LogTabsPanel',
	'StepInfoPanel',
	'TaskInputPanel',
	'TaskHistoryEntry',
	'ApprovalDialog',
]
