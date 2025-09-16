"""Browser class for high-level CDP operations."""

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
	from cdp_use.cdp.network.library import GetCookiesParameters
	from cdp_use.cdp.network.types import Cookie
	from cdp_use.cdp.target.commands import (
		CloseTargetParameters,
		CreateTargetParameters,
	)
	from cdp_use.client import CDPClient

	from .target import Target


class Browser:
	"""High-level browser interface built on CDP."""

	def __init__(self, client: 'CDPClient'):
		self._client = client

	async def newTarget(self, url: str | None = None) -> 'Target':
		"""Create a new target (tab)."""
		params: 'CreateTargetParameters' = {'url': url or 'about:blank'}
		result = await self._client.send.Target.createTarget(params)

		target_id = result['targetId']

		# Import here to avoid circular import
		from .target import Target

		return Target(self._client, target_id)

	async def getTargets(self) -> list['Target']:
		"""Get all available targets."""
		result = await self._client.send.Target.getTargets()

		targets = []
		# Import here to avoid circular import
		from .target import Target

		for target_info in result['targetInfos']:
			if target_info['type'] in ['page', 'iframe']:
				targets.append(Target(self._client, target_info['targetId']))

		return targets

	async def closeTarget(self, target: Union['Target', str]) -> None:
		"""Close a target by Target object or target ID."""
		# Import here to avoid circular import
		from .target import Target

		if isinstance(target, Target):
			target_id = target._target_id
		else:
			target_id = str(target)

		params: 'CloseTargetParameters' = {'targetId': target_id}
		await self._client.send.Target.closeTarget(params)

	async def cookies(self, urls: list[str] | None = None) -> list[Cookie]:
		"""Get cookies, optionally filtered by URLs."""
		params: GetCookiesParameters = {}
		if urls:
			params['urls'] = urls

		result = await self._client.send.Network.getCookies(params)
		return result['cookies']

	async def clearCookies(self) -> None:
		"""Clear all cookies."""
		await self._client.send.Network.clearBrowserCookies()
