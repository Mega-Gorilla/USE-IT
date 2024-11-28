from dataclasses import dataclass
from typing import Any, Optional

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

	def to_dict(self) -> dict[str, Any]:
		data = {}
		data['tabs'] = [tab.model_dump() for tab in self.tabs]
		data['screenshot'] = self.screenshot
		data['element_tree'] = None
		data['interacted_element'] = None
		data['url'] = self.url
		data['title'] = self.title
		return data


class BrowserError(Exception):
	"""Base class for all browser errors"""
