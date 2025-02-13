import asyncio
import time

from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
from browser_use.utils import time_execution_sync


async def test_process_html_file():
	config = BrowserContextConfig(
		cookies_file='cookies3.json',
		disable_security=True,
		wait_for_network_idle_page_load_time=2,
	)

	browser = Browser(
		config=BrowserConfig(
			# chrome_instance_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		)
	)
	context = BrowserContext(browser=browser, config=config)  # noqa: F821

	websites = [
		'https://csigna-eup1u6rnd.a99d1.metricstream.com/ui/form/MS_GRC_RISK/create',
		'https://kayak.com/flights',
		'https://immobilienscout24.de',
		'https://google.com',
		'https://amazon.com',
		'https://github.com',
	]

	async with context as context:
		page = await context.get_current_page()
		dom_service = DomService(page)

		for website in websites:
			print(f'\n{"=" * 50}\nTesting {website}\n{"=" * 50}')
			await page.goto(website)
			time.sleep(2)  # Additional wait for dynamic content

			async def test_viewport(expansion: int, description: str):
				print(f'\n{description}:')
				dom_state = await time_execution_sync(f'get_clickable_elements ({description})')(
					dom_service.get_clickable_elements
				)(highlight_elements=True, viewport_expansion=expansion)

				elements = dom_state.element_tree
				selector_map = dom_state.selector_map
				element_count = len(selector_map.keys())
				token_count = count_string_tokens(elements.clickable_elements_to_string(), model='gpt-4o')

				print(f'Number of elements: {element_count}')
				print(f'Token count: {token_count}')
				return element_count, token_count

			expansions = [0, 100, 200, 300, 400, 500, 600, 1000, -1, -200]
			results = []

			for i, expansion in enumerate(expansions):
				description = (
					f'{i + 1}. Expansion {expansion}px' if expansion >= 0 else f'{i + 1}. All elements ({expansion} expansion)'
				)
				count, tokens = await test_viewport(expansion, description)
				results.append((count, tokens))
				input('Press Enter to continue...')
				await page.evaluate('document.getElementById("playwright-highlight-container")?.remove()')

			# Print comparison summary
			print('\nComparison Summary:')
			for i, (count, tokens) in enumerate(results):
				expansion = expansions[i]
				description = f'Expansion {expansion}px' if expansion >= 0 else 'All elements (-1)'
				initial_count, initial_tokens = results[0]
				print(f'{description}: {count} elements (+{count - initial_count}), {tokens} tokens')

			input('\nPress Enter to continue to next website...')

			# Clear highlights before next website
			await page.evaluate('document.getElementById("playwright-highlight-container")?.remove()')


async def test_focus_vs_all_elements():
	config = BrowserContextConfig(
		cookies_file='cookies3.json',
		disable_security=True,
		wait_for_network_idle_page_load_time=2,
	)

	browser = Browser(
		config=BrowserConfig(
			# chrome_instance_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		)
	)
	context = BrowserContext(browser=browser, config=config)  # noqa: F821

	websites = [
		'https://csigna-eup1u6rnd.a99d1.metricstream.com/ui/form/MS_GRC_RISK/create',
		'https://kayak.com/flights',
		'https://immobilienscout24.de',
		'https://google.com',
		'https://amazon.com',
		'https://github.com',
	]

	async with context as context:
		page = await context.get_current_page()
		dom_service = DomService(page)

		for website in websites:
			# sleep 2
			await page.goto(website)
			time.sleep(2)

			while True:
				print(f'\n{"=" * 50}\nTesting {website}\n{"=" * 50}')
				time.sleep(2)  # Additional wait for dynamic content

				# First get all elements
				print('\nGetting all elements:')
				all_elements_state = await time_execution_sync('get_all_elements')(dom_service.get_clickable_elements)(
					highlight_elements=True, viewport_expansion=0
				)

				selector_map = all_elements_state.selector_map
				total_elements = len(selector_map.keys())
				print(f'Total number of elements: {total_elements}')

				answer = input('Which element do you want to focus on? (Enter index): ')
				if answer == 'q':
					break
				await page.evaluate('document.getElementById("playwright-highlight-container")?.remove()')

				focus_element = int(answer)
				focus_state = await time_execution_sync('get_focused_element')(dom_service.get_clickable_elements)(
					highlight_elements=True, focus_element=focus_element, viewport_expansion=0
				)
				focus_selector_map = focus_state.selector_map
				focus_element_count = len(focus_selector_map.keys())
				print(f'Number of highlighted elements when focused: {focus_element_count}')

				input('Press Enter to clear highlights and continue...')
				await page.evaluate('document.getElementById("playwright-highlight-container")?.remove()')


if __name__ == '__main__':
	asyncio.run(test_focus_vs_all_elements())
	asyncio.run(test_process_html_file())
