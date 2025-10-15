import asyncio
import logging


class PauseController:
	"""Manage external pause/resume coordination for the Agent run loop."""

	def __init__(self, logger: logging.Logger) -> None:
		self._logger = logger
		self._event = asyncio.Event()
		self._event.set()

	def pause(self) -> None:
		"""Pause the agent execution until resume() is called."""
		print('\n\n⏸️ Paused the agent and left the browser open.\n\tPress [Enter] to resume or [Ctrl+C] again to quit.')
		self._logger.debug('Pause requested; blocking run loop until resume is triggered.')
		self._event.clear()

	def resume(self) -> None:
		"""Resume the agent execution loop."""
		print('----------------------------------------------------------------------')
		print('▶️  Resuming agent execution where it left off...\n')
		self._logger.debug('Resume requested; releasing run loop.')
		self._event.set()

	async def wait_if_paused(self) -> None:
		"""Suspend until the agent is resumed if currently paused."""
		if not self._event.is_set():
			await self._event.wait()

	def force_resume(self) -> None:
		"""Ensure the run loop can proceed (used by cleanup paths)."""
		self._event.set()

	@property
	def is_paused(self) -> bool:
		return not self._event.is_set()
