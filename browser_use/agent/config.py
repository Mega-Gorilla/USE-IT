from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, TypeVar

from bubus import EventBus

from browser_use import Browser, BrowserProfile, BrowserSession
from browser_use.agent.views import AgentHistoryList, AgentOutput, AgentState, AgentStructuredOutput
from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import ContentPartImageParam, ContentPartTextParam
from browser_use.sync import CloudSync
from browser_use.telemetry.service import ProductTelemetry
from browser_use.tools.service import Tools

if TYPE_CHECKING:
	from browser_use.browser.views import BrowserStateSummary
	from browser_use.agent.service import Agent


AgentHook = Callable[['Agent'], Awaitable[None]]


@dataclass
class AgentFactories:
	"""Factory hooks for Agent dependencies to improve testability."""

	telemetry_factory: Callable[['Agent'], ProductTelemetry] = field(default=lambda agent: ProductTelemetry())
	event_bus_factory: Callable[['Agent'], EventBus] = field(
		default=lambda agent: EventBus(name=f'Agent_{str(agent.id)[-4:]}')
	)
	cloud_sync_factory: Callable[['Agent'], CloudSync | None] = field(default=lambda agent: CloudSync())


@dataclass
class AgentConfig:
	"""Aggregate configuration used to construct an Agent."""

	task: str
	llm: BaseChatModel | None = None
	# Browser/session configuration
	browser_profile: BrowserProfile | None = None
	browser_session: BrowserSession | None = None
	browser: Browser | None = None
	# Tooling
	tools: Tools[Any] | None = None
	controller: Tools[Any] | None = None
	# Initial execution options
	sensitive_data: dict[str, str | dict[str, str]] | None = None
	initial_actions: list[dict[str, dict[str, Any]]] | None = None
	register_new_step_callback: (
		Callable[['BrowserStateSummary', AgentOutput, int], None]
		| Callable[['BrowserStateSummary', AgentOutput, int], Awaitable[None]]
		| None
	) = None
	register_done_callback: (
		Callable[[AgentHistoryList], Awaitable[None]]
		| Callable[[AgentHistoryList], None]
		| None
	) = None
	register_external_agent_status_raise_error_callback: Callable[[], Awaitable[bool]] | None = None
	register_should_stop_callback: Callable[[], Awaitable[bool]] | None = None
	# Behavioural settings
	output_model_schema: type[AgentStructuredOutput] | None = None
	use_vision: bool | Literal['auto'] = 'auto'
	save_conversation_path: str | Path | None = None
	save_conversation_path_encoding: str | None = 'utf-8'
	max_failures: int = 3
	override_system_message: str | None = None
	extend_system_message: str | None = None
	generate_gif: bool | str = False
	available_file_paths: list[str] | None = None
	include_attributes: list[str] | None = None
	max_actions_per_step: int = 10
	use_thinking: bool = True
	flash_mode: bool = False
	max_history_items: int | None = None
	page_extraction_llm: BaseChatModel | None = None
	injected_agent_state: AgentState | None = None
	source: str | None = None
	file_system_path: str | None = None
	task_id: str | None = None
	cloud_sync: CloudSync | None = None
	calculate_cost: bool = False
	display_files_in_done_text: bool = True
	include_tool_call_examples: bool = False
	vision_detail_level: Literal['auto', 'low', 'high'] = 'auto'
	llm_timeout: int | None = None
	step_timeout: int = 120
	directly_open_url: bool = True
	include_recent_events: bool = False
	sample_images: list[ContentPartTextParam | ContentPartImageParam] | None = None
	final_response_after_failure: bool = True
	url_shortening_limit: int = 25
	extra: dict[str, Any] = field(default_factory=dict)
	factories: AgentFactories = field(default_factory=AgentFactories)
