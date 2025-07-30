"""Local browser connection that launches and manages browser processes."""

from __future__ import annotations

import asyncio
import os
import signal
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, PrivateAttr, model_validator
from uuid_extensions import uuid7str

from browser_use.browser.remote import RemoteBrowserConnection

if TYPE_CHECKING:
	from browser_use.browser.profile import BrowserProfile


class LocalBrowserConnection(RemoteBrowserConnection):
	"""Local browser connection that launches a browser process then connects via CDP.
	
	This class extends RemoteBrowserConnection to add browser process management.
	It launches a local browser instance and then connects to it via CDP.
	"""
	
	# Override cdp_url to be optional since we'll set it after launch
	cdp_url: str | None = None  # type: ignore[assignment]
	
	# Process management
	_subprocess: asyncio.subprocess.Process | None = PrivateAttr(default=None)
	_browser_pid: int | None = PrivateAttr(default=None)
	_temp_dir: tempfile.TemporaryDirectory | None = PrivateAttr(default=None)
	_user_data_dir: Path | None = PrivateAttr(default=None)
	
	@model_validator(mode='after')
	def validate_local_browser(self) -> Self:
		"""Local browser doesn't need cdp_url at init time."""
		# Override parent validator - cdp_url not required for local browser
		return self
	
	@classmethod
	def from_existing_pid(
		cls,
		browser_profile: BrowserProfile,
		pid: int,
		cdp_url: str,
		**kwargs: Any,
	) -> LocalBrowserConnection:
		"""Create a connection from an existing browser process.
		
		Args:
			browser_profile: Browser configuration profile
			pid: Process ID of the existing browser
			cdp_url: CDP URL to connect to the browser
			**kwargs: Additional arguments for the session
		"""
		session = cls(
			browser_profile=browser_profile,
			cdp_url=cdp_url,
			**kwargs,
		)
		session._browser_pid = pid
		# Mark that we didn't launch this process
		session._subprocess = None
		return session
	
	async def start(self) -> Self:
		"""Launch browser process if needed, then connect via CDP."""
		if self._started:
			return self
		
		# If no CDP URL, we need to launch the browser
		if not self.cdp_url:
			await self._launch_browser()
		
		# Connect via parent class
		await super().start()
		
		# Get the browser PID via CDP if we launched it
		if self._browser_pid is None and self._browser:
			await self._get_browser_pid_via_cdp()
		
		return self
	
	async def _launch_browser(self) -> None:
		"""Launch the browser subprocess and get its CDP URL."""
		# Set up user data directory
		await self._setup_user_data_dir()
		
		# Get launch args from profile
		launch_args = self.browser_profile.args_for_browser_launch()
		
		# Add debugging port
		debug_port = self._find_free_port()
		launch_args.extend([
			f'--remote-debugging-port={debug_port}',
			f'--user-data-dir={self._user_data_dir}',
		])
		
		# Get browser executable from playwright
		# We need playwright started to get the browser path
		self._playwright = await async_playwright().start()
		
		# Use custom executable if provided, otherwise use playwright's
		if self.browser_profile.executable_path:
			browser_path = self.browser_profile.executable_path
		else:
			browser_path = self._playwright.chromium.executable_path
		
		# Launch browser subprocess directly
		self._subprocess = await asyncio.create_subprocess_exec(
			browser_path,
			*launch_args,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		
		# Wait for CDP to be ready and get the URL
		self.cdp_url = await self._wait_for_cdp_url(debug_port)
		
		# We'll get the actual PID via CDP after connecting
	
	async def _setup_user_data_dir(self) -> None:
		"""Set up the user data directory for the browser."""
		user_data_dir = self.browser_profile.user_data_dir
		
		if user_data_dir is None:
			# Use temp directory
			self._temp_dir = tempfile.TemporaryDirectory(prefix='browser_use_')
			self._user_data_dir = Path(self._temp_dir.name)
		else:
			# Use specified directory
			self._user_data_dir = Path(user_data_dir)
			self._user_data_dir.mkdir(parents=True, exist_ok=True)
	
	
	def _find_free_port(self) -> int:
		"""Find a free port for the debugging interface."""
		import socket
		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
			s.bind(('', 0))
			s.listen(1)
			port = s.getsockname()[1]
		return port
	
	async def _wait_for_cdp_url(self, port: int, timeout: float = 30) -> str:
		"""Wait for the browser to start and return the CDP URL."""
		import aiohttp
		
		start_time = asyncio.get_event_loop().time()
		
		while asyncio.get_event_loop().time() - start_time < timeout:
			try:
				async with aiohttp.ClientSession() as session:
					async with session.get(f'http://localhost:{port}/json/version') as resp:
						if resp.status == 200:
							data = await resp.json()
							ws_url = data.get('webSocketDebuggerUrl', '')
							# Convert ws:// to http:// for CDP
							return ws_url.replace('ws://', 'http://').replace('/devtools/browser/', '')
			except:
				# Browser not ready yet
				await asyncio.sleep(0.1)
		
		raise TimeoutError(f"Browser did not start within {timeout} seconds")
	
	async def _get_browser_pid_via_cdp(self) -> None:
		"""Get the browser process ID via CDP SystemInfo.getProcessInfo."""
		if not self._browser:
			return
		
		# Get any page to send CDP command
		pages = self._context.pages if self._context else []
		if not pages:
			# Create a temporary page just to get CDP access
			page = await self._context.new_page()
			try:
				cdp_session = await page.context.new_cdp_session(page)
				result = await cdp_session.send('SystemInfo.getProcessInfo')
				self._browser_pid = result.get('processInfo', {}).get('id')
			finally:
				await page.close()
		else:
			# Use existing page
			cdp_session = await pages[0].context.new_cdp_session(pages[0])
			result = await cdp_session.send('SystemInfo.getProcessInfo')
			self._browser_pid = result.get('processInfo', {}).get('id')
	
	async def stop(self) -> None:
		"""Stop the browser process and clean up resources."""
		# First disconnect via parent
		await super().stop()
		
		# Then terminate the subprocess if we launched it
		if self._subprocess:
			try:
				# Try graceful shutdown first
				self._subprocess.terminate()
				await asyncio.wait_for(self._subprocess.wait(), timeout=5.0)
			except asyncio.TimeoutError:
				# Force kill if needed
				if self._browser_pid:
					try:
						os.kill(self._browser_pid, signal.SIGKILL)
					except ProcessLookupError:
						pass
			
			self._subprocess = None
			self._browser_pid = None
		
		# Clean up temp directory
		if self._temp_dir:
			self._temp_dir.cleanup()
			self._temp_dir = None
		
		self._user_data_dir = None
	
	@property
	def browser_pid(self) -> int | None:
		"""Get the browser process ID."""
		return self._browser_pid
	
	@property
	def is_process_running(self) -> bool:
		"""Check if the browser process is still running."""
		if not self._browser_pid:
			return False
		
		try:
			# Check if process exists (doesn't actually kill it with signal 0)
			os.kill(self._browser_pid, 0)
			return True
		except ProcessLookupError:
			return False