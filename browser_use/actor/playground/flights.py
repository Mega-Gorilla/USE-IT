import asyncio

from browser_use import Agent, Browser, ChatOpenAI

llm = ChatOpenAI('gpt-4.1-mini')


async def main():
	"""
	Main function demonstrating mixed automation with Browser-Use and Playwright.
	"""
	print('🚀 Mixed Automation with Browser-Use and Actor API')

	browser = Browser(keep_alive=True)
	await browser.start()

	target = await browser.get_current_target() or await browser.new_target()

	# Go to apple wikipedia page
	await target.goto('https://www.google.com/travel/flights')

	await asyncio.sleep(1)

	round_trip_button = await target.must_get_element_by_prompt('round trip button', llm)
	await round_trip_button.click()

	one_way_button = await target.must_get_element_by_prompt('one way button', llm)
	await one_way_button.click()

	await asyncio.sleep(1)

	agent = Agent(task='Find the cheapest flight from London to Paris on 2025-10-15', llm=llm, browser_session=browser)
	await agent.run()

	input('Press Enter to continue...')

	await browser.stop()


if __name__ == '__main__':
	asyncio.run(main())
