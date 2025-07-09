import asyncio
import time

import httpx
from playwright.async_api import async_playwright

from browser_use.browser import Browser
from browser_use.dom.service import DOMService


async def main():
	async with async_playwright() as p:
		playwright_browser = await p.chromium.launch(args=['--remote-debugging-port=9222'], headless=False)
		browser = Browser(browser=playwright_browser)

		async with httpx.AsyncClient() as client:
			version_info = await client.get('http://localhost:9222/json/version')
			browser.cdp_url = version_info.json()['webSocketDebuggerUrl']

		await browser.create_new_tab('https://en.wikipedia.org/wiki/Apple_Inc.')
		await browser._wait_for_page_and_frames_load()

		dom_service = DOMService(browser)

		start = time.time()
		dom_tree = await dom_service.get_dom_tree()
		end = time.time()
		print(f'Time taken: {end - start} seconds')

		# async with aiofiles.open('tmp/snapshot.json', 'w') as f:
		# 	await f.write(json.dumps(snapshot, indent=1))

		# async with aiofiles.open('tmp/dom_tree.json', 'w') as f:
		# 	await f.write(json.dumps(dom_tree, indent=1))

		# async with aiofiles.open('tmp/ax_tree.json', 'w') as f:
		# 	await f.write(json.dumps(ax_tree, indent=1))

		# print('saved dom tree to tmp/dom_tree.json')
		# print('saved snapshot to tmp/snapshot.json')
		# print('saved ax tree to tmp/ax_tree.json')

		print('Done')


if __name__ == '__main__':
	asyncio.run(main())
