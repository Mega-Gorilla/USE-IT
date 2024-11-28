import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from langchain_openai import ChatOpenAI

from browser_use import Agent, Browser, Controller

# Initialize controller first
controller = Controller()


@controller.action(
	'Upload file - the file name is inside the function - you only need to call this with the  correct index',
	requires_browser=True,
)
async def upload_file(index: int, browser: Browser):
	element = await browser.get_element_by_index(index)
	my_file = Path.cwd() / 'examples/test_cv.txt'
	if not element:
		raise Exception(f'Element with index {index} not found')

	await element.set_input_files(str(my_file.absolute()))
	return f'Uploaded file to index {index}'


@controller.action('Close file dialog', requires_browser=True)
async def close_file_dialog(browser: Browser):
	page = await browser.get_current_page()
	await page.keyboard.press('Escape')


async def main():
	sites = [
		'https://practice.expandtesting.com/upload',
	]
	task = f'go to {" ".join(sites)} each in new tabs and Upload my file then subbmit extract the page content and go to google and find elon musk and stop'

	model = ChatOpenAI(model='gpt-4o')
	agent = Agent(
		task=task,
		llm=model,
		controller=controller,
	)

	await agent.run()

	history_file_path = 'AgentHistoryList.json'
	agent.save_history(file_path=history_file_path)

	agent2 = Agent(llm=model, controller=controller, task='')
	await agent2.load_and_rerun(history_file_path)

	input('Press Enter to close...')


if __name__ == '__main__':
	asyncio.run(main())
