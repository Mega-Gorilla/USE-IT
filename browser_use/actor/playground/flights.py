import asyncio

from browser_use import Browser, ChatOpenAI

llm = ChatOpenAI('gpt-4.1-mini')


async def main():
	"""
	Main function demonstrating mixed automation with Browser-Use and Playwright.
	"""
	print('🚀 Mixed Automation with Browser-Use and Actor API')

	browser = Browser(keep_alive=True)
	await browser.start()

	target = await browser.get_current_target() or await browser.newTarget()

	# Go to apple wikipedia page
	await target.goto('https://www.google.com/travel/flights')

	await asyncio.sleep(2)

	round_trip_button = await target.mustGetElementByPrompt('round trip button', llm)
	await round_trip_button.click()

	one_way_button = await target.mustGetElementByPrompt('one way button', llm)
	await one_way_button.click()

	await asyncio.sleep(0.5)

	await browser.stop()


if __name__ == '__main__':
	asyncio.run(main())
