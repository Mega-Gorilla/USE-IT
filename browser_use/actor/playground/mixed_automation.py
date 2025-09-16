import asyncio

from pydantic import BaseModel

from browser_use import Agent, Browser

TASK = """
On the current wikipedia page, find the latest huge edit and tell me what is was about.
"""


class LatestEditFinder(BaseModel):
	"""Find the latest huge edit on the current wikipedia page."""

	latest_edit: str
	edit_time: str
	edit_author: str
	edit_summary: str
	edit_url: str


async def main():
	"""
	Main function demonstrating mixed automation with Browser-Use and Playwright.
	"""
	print('🚀 Mixed Automation with Browser-Use and Actor API')

	browser = Browser(keep_alive=True)
	await browser.start()

	apple_target = await browser.get_current_target() or await browser.newTarget()

	# Go to apple wikipedia page
	await apple_target.goto('https://en.wikipedia.org/wiki/Apple_Inc.')

	await asyncio.sleep(1)

	element = await apple_target.getElementsByCSSSelector(
		'#mw-content-text > div.mw-content-ltr.mw-parser-output > p:nth-child(8) > a:nth-child(3)'
	)

	if first_element := element[0]:
		print('Element found', first_element)
		await first_element.click()
	else:
		print('No element found')

	agent = Agent(
		task='clic on the button that says "technology company"',
		browser=browser,
	)
	output = await agent.run(max_steps=1)

	await asyncio.sleep(10)

	await browser.stop()


if __name__ == '__main__':
	asyncio.run(main())
