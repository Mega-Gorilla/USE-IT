from cdp_use import CDPClient

from browser_use.browser import Browser


class DOMService:
	def __init__(self, browser: Browser):
		self.browser = browser

		self.cdp_client: CDPClient | None = None
		self.playwright_page_to_session_id_store: dict[str, str] = {}

	async def _get_cdp_client(self) -> CDPClient:
		if self.cdp_client is None:
			if not self.browser.cdp_url:
				raise ValueError('CDP URL is not set')

			self.cdp_client = CDPClient(self.browser.cdp_url)
			await self.cdp_client.start()
		return self.cdp_client

	# on self destroy -> stop the cdp client
	async def __del__(self):
		if self.cdp_client:
			await self.cdp_client.stop()
			self.cdp_client = None

	async def _get_current_page_session_id(self) -> str:
		"""Get the target ID for a playwright page.

		TODO: this is a REALLY hacky way -> if multiple same urls are open then this will break
		"""
		page = await self.browser.get_current_page()
		page_guid = page._impl_obj._guid

		# if page_guid in self.page_to_session_id_store:
		# 	return self.page_to_session_id_store[page_guid]

		cdp_client = await self._get_cdp_client()

		targets = await cdp_client.send.Target.getTargets()
		for target in targets['targetInfos']:
			if target['type'] == 'page' and target['url'] == page.url:
				# cache the session id for this playwright page
				self.playwright_page_to_session_id_store[page_guid] = target['targetId']

				session = await cdp_client.send.Target.attachToTarget(params={'targetId': target['targetId'], 'flatten': True})
				session_id = session['sessionId']

				await cdp_client.send.Target.setAutoAttach(
					params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
				)

				await cdp_client.send.DOM.enable(session_id=session_id)
				await cdp_client.send.Accessibility.enable(session_id=session_id)

				return session_id

		raise ValueError(f'No session id found for page {page.url}')

	async def get_dom_tree(self):
		if not self.browser.cdp_url:
			raise ValueError('CDP URL is not set')

		session_id = await self._get_current_page_session_id()
		cdp_client = await self._get_cdp_client()

		snapshot = await cdp_client.send.DOMSnapshot.captureSnapshot(params={'computedStyles': []}, session_id=session_id)

		dom_tree = await cdp_client.send.DOM.getDocument(params={'depth': -1, 'pierce': True}, session_id=session_id)

		ax_tree = await cdp_client.send.Accessibility.getFullAXTree(session_id=session_id)

		return snapshot, dom_tree, ax_tree
