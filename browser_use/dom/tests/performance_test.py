import gc
import time

from memory_profiler import memory_usage

from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.dom.service import DomService

sites = [
	'https://kayak.com/flights',
	'https://google.com/flights',
	'https://immobilienscout24.de',
	'https://seleniumbase.io/w3schools/iframes',
	'https://www.lego.com',
]


async def test_process_dom():
	browser = Browser(config=BrowserConfig(headless=False))

	async with await browser.new_context() as context:
		page = await context.get_current_page()

		for site in sites:
			print(f'Processing {site}...')

			await page.goto(site)
			time.sleep(3)

			memory_before = memory_usage(-1, interval=0.1, timeout=1)[0]
			print(f'Memory before: {memory_before} MB')

			dom_service = DomService(page)

			start = time.time()
			dom_tree = await dom_service.get_clickable_elements()
			memory_after = memory_usage(-1, interval=0.1, timeout=1)[0]
			print(f'Memory after: {memory_after} MB')

			memory_diff = memory_after - memory_before
			print(f'Memory diff: {memory_diff} MB')

			end = time.time()

			print(f'Time taken: {end - start:.2f}s')

			gc.collect()


if __name__ == '__main__':
	import asyncio

	asyncio.run(test_process_dom())
