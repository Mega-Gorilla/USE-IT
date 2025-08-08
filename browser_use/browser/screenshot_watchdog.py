"""Screenshot watchdog for handling screenshot requests using CDP."""

from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent

from browser_use.browser.events import ScreenshotEvent
from browser_use.browser.watchdog_base import BaseWatchdog
from cdp_use.cdp.page import CaptureScreenshotParameters

if TYPE_CHECKING:
	pass


class ScreenshotWatchdog(BaseWatchdog):
	"""Handles screenshot requests using CDP."""

	# Events this watchdog listens to
	LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [ScreenshotEvent]

	# Events this watchdog emits
	EMITS: ClassVar[list[type[BaseEvent[Any]]]] = []

	async def on_ScreenshotEvent(self, event: ScreenshotEvent) -> dict[str, str | None]:
		"""Handle screenshot request using CDP.

		Args:
			event: ScreenshotEvent with optional full_page and clip parameters

		Returns:
			Dict with 'screenshot' key containing base64-encoded screenshot or None
		"""
		try:
			# Get CDP client and session for current target
			cdp_session = await self.browser_session.attach_cdp_session()

			# Prepare screenshot parameters
			params = CaptureScreenshotParameters(format='png', captureBeyondViewport=False)

			# Take screenshot using CDP
			self.logger.debug(f'[ScreenshotWatchdog] Taking screenshot with params: {params}')
			result = await cdp_session.cdp_client.send.Page.captureScreenshot(
				params=params,
				session_id=cdp_session.session_id
			)

			# Return base64-encoded screenshot data
			if result and 'data' in result:
				self.logger.debug('[ScreenshotWatchdog] Screenshot captured successfully')
				return {'screenshot': result['data']}
			else:
				self.logger.warning('[ScreenshotWatchdog] Screenshot result missing data')
				return {'screenshot': None}

		except Exception as e:
			self.logger.error(f'[ScreenshotWatchdog] Screenshot failed: {e}')
			return {'screenshot': None}
