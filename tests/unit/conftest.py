import logging
from typing import Any

import pytest


class DummyActionModel:
	"""シンプルな Pydantic 互換アクションモデルのスタブ。"""

	def __init__(self, **data: Any) -> None:
		super().__setattr__('_data', {})
		for key, value in data.items():
			self.__setattr__(key, value)

	def __setattr__(self, key: str, value: Any) -> None:
		if key == '_data':
			super().__setattr__(key, value)
		else:
			self._data[key] = value
			super().__setattr__(key, value)

	def model_dump(self, exclude_unset: bool = True, mode: str | None = None) -> dict[str, Any]:
		return dict(self._data)

	def get_index(self) -> Any:
		return self._data.get('index')

	def set_index(self, value: Any) -> None:
		self._data['index'] = value


@pytest.fixture(scope='session')
def dummy_action_model_class():
	return DummyActionModel


@pytest.fixture(scope='session')
def test_logger():
	logger = logging.getLogger('browser_use.tests.unit')
	logger.setLevel(logging.DEBUG)
	return logger
