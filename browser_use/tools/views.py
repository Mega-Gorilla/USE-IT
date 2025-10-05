from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


# Action Input Models
class SearchAction(BaseModel):
	query: str
	search_engine: str = Field(default='duckduckgo', description='duckduckgo, google, bing')


# Backward compatibility alias
SearchAction = SearchAction


class GoToUrlAction(BaseModel):
	url: str
	new_tab: bool = Field(default=False)


class ClickElementAction(BaseModel):
	index: int = Field(ge=1, description='from browser_state')
	ctrl: bool | None = Field(
		default=None,
		description='True=New background tab (Ctrl+Click)',
	)
	# expect_download: bool = Field(default=False, description='set True if expecting a download, False otherwise')  # moved to downloads_watchdog.py
	# click_count: int = 1  # TODO


class InputTextAction(BaseModel):
	index: int = Field(ge=1, description='from browser_state')
	text: str
	clear_existing: bool = Field(default=True, description='True to clear, False to append')


class DoneAction(BaseModel):
	text: str = Field(description='summary for user')
	success: bool = Field(description='True if completed')
	files_to_display: list[str] | None = Field(default=[], description='files to display')


T = TypeVar('T', bound=BaseModel)


class StructuredOutputAction(BaseModel, Generic[T]):
	success: bool = Field(default=True, description='True if finished, False if not')
	data: T


class SwitchTabAction(BaseModel):
	tab_id: str = Field(min_length=4, max_length=4, description="from browser_state ('Tab <tab_id>')")


class CloseTabAction(BaseModel):
	tab_id: str = Field(min_length=4, max_length=4, description="from browser_state ('Tab <tab_id>')")


class ScrollAction(BaseModel):
	down: bool = Field(description='True=down, False=up')
	num_pages: float = Field(default=1.0, description='pages to scroll (0.5=half, 1=page, 10=bottom)')
	frame_element_index: int | None = Field(default=None, description='index for specific container')


class SendKeysAction(BaseModel):
	keys: str = Field(description='keys (Escape, Enter, PageDown) or shortcuts (Control+o)')


class UploadFileAction(BaseModel):
	index: int = Field(description='from browser_state')
	path: str


class ExtractPageContentAction(BaseModel):
	value: str


class NoParamsAction(BaseModel):
	"""Accepts any input, discards it, returns empty model."""

	model_config = ConfigDict(extra='ignore')


class GetDropdownOptionsAction(BaseModel):
	index: int = Field(ge=1, description='dropdown from browser_state')


class SelectDropdownOptionAction(BaseModel):
	index: int = Field(ge=1, description='dropdown from browser_state')
	text: str = Field(description='exact text/value to select')
