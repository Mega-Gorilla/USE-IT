import asyncio

from browser_use.browser import BrowserProfile, BrowserSession


async def test():
	print('Testing browser window sizing with no_viewport=False...')
	profile = BrowserProfile(window_width=1440, window_height=900, no_viewport=False, headless=False)
	browser_session = BrowserSession(profile)
	await browser_session.start()
	page = await browser_session.get_current_page()
	await page.goto('https://example.com')
	await asyncio.sleep(2)
	viewport = await page.evaluate('() => ({width: window.innerWidth, height: window.innerHeight})')
	print('Configured size: width=1440, height=900')
	print(f'Actual viewport size: {viewport}')

	# Get the actual window size
	window_size = await page.evaluate("""
        () => ({
            width: window.outerWidth,
            height: window.outerHeight
        })
    """)
	print(f'Actual window size: {window_size}')

	await browser_session.stop()


if __name__ == '__main__':
	asyncio.run(test())
