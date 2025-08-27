import pytest

from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile


async def test_connection_via_cdp():
	browser_session = BrowserSession(
		cdp_url='http://localhost:9898',
		browser_profile=BrowserProfile(
			headless=True,
			keep_alive=True,
		),
	)
	with pytest.raises(Exception) as e:
		await browser_session.start()

	# Assert on the exception value outside the context manager
	assert 'ECONNREFUSED' in str(e.value)

	# This test requires Playwright to create a browser instance with CDP enabled
	# For now, skip this test as we've moved away from Playwright dependencies
	pytest.skip('Test requires Playwright integration which has been removed')
