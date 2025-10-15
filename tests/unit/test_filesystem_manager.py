import asyncio
from types import SimpleNamespace

import pytest

from browser_use.agent.filesystem_manager import FilesystemManager
from browser_use.filesystem.file_system import FileSystem


def make_state():
	return SimpleNamespace(file_system_state=None)


def make_browser_session(downloads_path: str | None, downloaded_files: list[str] | None = None):
	profile = SimpleNamespace(downloads_path=downloads_path)
	session = SimpleNamespace(browser_profile=profile)
	session.downloaded_files = downloaded_files or []
	return session


def test_initialize_from_new_directory(tmp_path, test_logger):
	state = make_state()
	session = make_browser_session(str(tmp_path))
	manager = FilesystemManager(
		state=state,
		browser_session=session,
		agent_directory=tmp_path,
		available_file_paths=[],
		file_system_path=None,
		logger=test_logger,
	)

	assert manager.file_system_path is not None
	assert state.file_system_state is not None


def test_restore_from_state(tmp_path, test_logger):
	file_system = FileSystem(tmp_path)
	state = SimpleNamespace(file_system_state=file_system.get_state())
	session = make_browser_session(str(tmp_path))

	manager = FilesystemManager(
		state=state,
		browser_session=session,
		agent_directory=tmp_path,
		available_file_paths=[],
		file_system_path=None,
		logger=test_logger,
	)

	assert manager.file_system_path == str(tmp_path)


@pytest.mark.asyncio
async def test_check_and_update_downloads_detects_new_file(tmp_path, test_logger):
	state = make_state()
	session = make_browser_session(str(tmp_path), downloaded_files=['/tmp/file1.txt'])
	manager = FilesystemManager(
		state=state,
		browser_session=session,
		agent_directory=tmp_path,
		available_file_paths=[],
		file_system_path=None,
		logger=test_logger,
	)

	await manager.check_and_update_downloads('after test')
	assert '/tmp/file1.txt' in manager.available_file_paths


def test_save_state_updates_state_object(tmp_path, test_logger):
	state = make_state()
	session = make_browser_session(str(tmp_path))
	manager = FilesystemManager(
		state=state,
		browser_session=session,
		agent_directory=tmp_path,
		available_file_paths=[],
		file_system_path=str(tmp_path),
		logger=test_logger,
	)

	manager.save_state()
	assert state.file_system_state is not None
