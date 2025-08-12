"""Watchdog for handling JavaScript dialogs (alert, confirm, prompt) automatically."""

import asyncio
from typing import ClassVar

from bubus import BaseEvent
from pydantic import PrivateAttr

from browser_use.browser.events import DialogOpenedEvent, TabCreatedEvent
from browser_use.browser.watchdog_base import BaseWatchdog


class PopupsWatchdog(BaseWatchdog):
	"""Handles JavaScript dialogs (alert, confirm, prompt) by automatically accepting them."""

	# Events this watchdog listens to and emits
	LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [TabCreatedEvent, DialogOpenedEvent]
	EMITS: ClassVar[list[type[BaseEvent]]] = [DialogOpenedEvent]

	# Track which targets have dialog handlers registered
	_dialog_listeners_registered: set[str] = PrivateAttr(default_factory=set)

	def __init__(self, **kwargs):
		super().__init__(**kwargs)
		self.logger.info(f'🚀 PopupsWatchdog initialized with browser_session={self.browser_session}, ID={id(self)}')

	async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
		"""Set up JavaScript dialog handling when a new tab is created."""
		target_id = event.target_id
		self.logger.info(f'🎯 PopupsWatchdog received TabCreatedEvent for target {target_id}')

		# Skip if we've already registered for this target
		if target_id in self._dialog_listeners_registered:
			self.logger.debug(f'Already registered dialog handlers for target {target_id}')
			return

		self.logger.info(f'📌 Starting dialog handler setup for target {target_id}')
		try:
			# Set up handler for JavaScript dialogs
			# CDP handlers are sync but we need to handle dialogs immediately to unblock the browser
			def handle_dialog(event_data, _session_id: str | None = None):
				self.browser_session.event_bus.dispatch(
					DialogOpenedEvent(
						frame_id=event_data.get('frameId'),
						dialog_type=event_data.get('type'),
						message=event_data.get('message'),
						url=event_data.get('url'),
					)
				)

			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
			cdp_session.cdp_client.register.Page.javascriptDialogOpening(handle_dialog)
			self.logger.info(
				f'✅ Successfully registered Page.javascriptDialogOpening handler for session {cdp_session.session_id}'
			)

			# Mark this target as having dialog handling set up
			self._dialog_listeners_registered.add(target_id)

			self.logger.info(f'✅ Set up JavaScript dialog handling for tab {target_id}')

		except Exception as e:
			self.logger.warning(f'Failed to set up dialog handling for tab {target_id}: {e}')

	async def on_DialogOpenedEvent(self, event: DialogOpenedEvent) -> None:
		"""Handle the async closing of JavaScript dialogs."""
		self.logger.info(f'📋 on_DialogOpenedEvent called with frame_id={event.frame_id} url={event.url} message={event.message}')

		assert self.browser_session.agent_focus is not None, 'Agent focus not set when handling DialogOpenedEvent'

		cdp_session = await self.browser_session.cdp_client_for_frame(event.frame_id)
		try:
			# delay to look more human
			await asyncio.sleep(0.25)
			assert self.browser_session._cdp_client_root
			await asyncio.wait_for(
				self.browser_session._cdp_client_root.send.Page.handleJavaScriptDialog(
					params={'accept': True},
					session_id=cdp_session.session_id,
				),
				timeout=5.0,
			)
			# CRITICAL: you must either wait for Page.javascriptDialogClosed or wait for some fixed time here
			await asyncio.sleep(0.2)
			assert (
				await cdp_session.cdp_client.send.Runtime.evaluate(
					params={'expression': '1+1'}, session_id=cdp_session.session_id
				)
			)['result'].get('value') == 2, 'Browser crashed after handling JS dialog popup'
			self.logger.info('✅ JS dialog popup handled successfully')
			# self.browser_session.agent_focus = await self.browser_session.get_or_create_cdp_session(current_focus.target_id, focus=True, new_socket=True)
			# assert await self.browser_session.agent_focus.cdp_client.send.Page.getFrameTree(session_id=self.browser_session.agent_focus.session_id) is not None, "Agent focus not set after handling dialog"
		except Exception as e:
			self.logger.error(f'Failed to handle JavaScript dialog: {e}')
		# finally:
		# 	self.event_bus.dispatch(AgentFocusChangedEvent(
		# 		tab_index=await self.browser_session.get_tab_index(current_focus.target_id),
		# 		url=current_focus.url,
		# 	))
