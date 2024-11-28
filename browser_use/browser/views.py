from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from browser_use.dom.views import DOMState


# Pydantic
class TabInfo(BaseModel):
	"""Represents information about a browser tab"""

	page_id: int
	url: str
	title: str


@dataclass
class BrowserState(DOMState):
	url: str
	title: str
	tabs: list[TabInfo]
	screenshot: Optional[str] = None

	# def model_dump(self) -> dict:
	# 	dump = super().model_dump()
	# 	# Add a summary of available tabs
	# 	if self.tabs:
	# 		dump['available_tabs'] = [
	# 			f'Tab {i+1}: {tab.title} ({tab.url})' for i, tab in enumerate(self.tabs)
	# 		]
	# 	return dump


class BrowserError(Exception):
	"""Base class for all browser errors"""
