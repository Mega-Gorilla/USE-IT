import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext


async def analyze_page_structure(url: str):
	"""Analyze and print the structure of a webpage"""
	browser = Browser(
		config=BrowserConfig(
			headless=False,  # Set to True if you don't need to see the browser
		)
	)

	context = BrowserContext(browser=browser)

	try:
		async with context as ctx:
			# Navigate to the URL
			page = await ctx.get_current_page()
			await page.goto(url)
			await page.wait_for_load_state('networkidle')

			# Get and print the page structure
			structure = await ctx.get_page_structure()
			print(f'\nPage Structure for {url}:\n')
			print(structure)

			input('Press Enter to close the browser...')
	finally:
		await browser.close()


if __name__ == '__main__':
	# You can modify this URL to analyze different pages
	url = 'https://reddit.com'
	asyncio.run(analyze_page_structure(url))
