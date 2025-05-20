import pytest
from playwright.async_api import async_playwright

from browser_use.browser import BrowserSession


@pytest.mark.asyncio
async def test_connection_via_cdp(monkeypatch):
	browser_session = BrowserSession(
		cdp_url='http://localhost:9222',
	)
	with pytest.raises(Exception) as e:
		await browser_session.start()

	# Assert on the exception value outside the context manager
	assert 'Failed to connect to browser' in str(e.value)

	playwright = await async_playwright().start()
	browser = await playwright.chromium.launch(args=['--remote-debugging-port=9222'])

	await browser_session.start()
	await browser_session.create_new_tab()

	assert (await browser_session.get_current_page()).url == 'about:blank'

	await browser_session.close()
	await browser.close()
