import logging
from pathlib import Path

from browser_use.agent.views import AgentState
from browser_use.browser.session import BrowserSession
from browser_use.filesystem.file_system import FileSystem


class FilesystemManager:
	"""Encapsulates filesystem setup, state tracking, and download monitoring for an Agent."""

	def __init__(
		self,
		*,
		state: AgentState,
		browser_session: BrowserSession,
		agent_directory: Path,
		available_file_paths: list[str],
		file_system_path: str | None,
		logger: logging.Logger,
	) -> None:
		self.state = state
		self.browser_session = browser_session
		self.agent_directory = agent_directory
		self.available_file_paths = list(available_file_paths)
		self.file_system_path: str | None = None
		self.file_system: FileSystem | None = None
		self._logger = logger

		self.has_downloads_path = self.browser_session.browser_profile.downloads_path is not None
		self._last_known_downloads: list[str] = []

		self._initialize_file_system(file_system_path)

		if self.has_downloads_path:
			self._logger.debug('📁 Initialized download tracking for agent')

	def _initialize_file_system(self, file_system_path: str | None) -> None:
		if self.state.file_system_state and file_system_path:
			raise ValueError(
				'Cannot provide both file_system_state (from agent state) and file_system_path. '
				'Either restore from existing state or create new file system at specified path, not both.'
			)

		if self.state.file_system_state:
			try:
				self.file_system = FileSystem.from_state(self.state.file_system_state)
				self.file_system_path = str(self.file_system.base_dir)
				self._logger.debug(f'💾 File system restored from state to: {self.file_system_path}')
				return
			except Exception as exc:
				self._logger.error(f'💾 Failed to restore file system from state: {exc}')
				raise

		try:
			if file_system_path:
				self.file_system = FileSystem(file_system_path)
				self.file_system_path = file_system_path
			else:
				self.file_system = FileSystem(self.agent_directory)
				self.file_system_path = str(self.agent_directory)
		except Exception as exc:
			self._logger.error(f'💾 Failed to initialize file system: {exc}.')
			raise

		self.state.file_system_state = self.file_system.get_state()
		self._logger.debug(f'💾 File system path: {self.file_system_path}')

	def save_state(self) -> None:
		"""Persist the current filesystem state back into AgentState."""
		if not self.file_system:
			self._logger.error('💾 File system is not set up. Cannot save state.')
			raise ValueError('File system is not set up. Cannot save state.')

		self.state.file_system_state = self.file_system.get_state()

	async def check_and_update_downloads(self, context: str = '') -> None:
		"""Refresh available file paths based on newly downloaded files."""
		if not self.has_downloads_path:
			return

		try:
			current_downloads = self.browser_session.downloaded_files
			if current_downloads != self._last_known_downloads:
				self._update_available_file_paths(current_downloads)
				self._last_known_downloads = current_downloads
				if context:
					self._logger.debug(f'📁 {context}: Updated available files')
		except Exception as exc:
			error_context = f' {context}' if context else ''
			self._logger.debug(f'📁 Failed to check for downloads{error_context}: {type(exc).__name__}: {exc}')

	def _update_available_file_paths(self, downloads: list[str]) -> None:
		if not self.has_downloads_path:
			return

		current_files = set(self.available_file_paths or [])
		new_files = set(downloads) - current_files

		if new_files:
			self.available_file_paths = list(current_files | new_files)
			self._logger.info(
				f'📁 Added {len(new_files)} downloaded files to available_file_paths (total: {len(self.available_file_paths)} files)'
			)
			for file_path in new_files:
				self._logger.info(f'📄 New file available: {file_path}')
		else:
			self._logger.debug(f'📁 No new downloads detected (tracking {len(current_files)} files)')
