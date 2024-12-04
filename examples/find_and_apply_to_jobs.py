"""
Find and apply to jobs.

@dev You need to add OPENAI_API_KEY to your environment variables.

Also you have to install PyPDF2 to read pdf files: pip install PyPDF2
"""

import csv
import os
import re
import sys
from pathlib import Path

from PyPDF2 import PdfReader

from browser_use.browser.browser import Browser, BrowserConfig

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from typing import List, Optional

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, SecretStr

from browser_use import ActionResult, Agent, Controller
from browser_use.browser.context import BrowserContext

load_dotenv()
import logging

logger = logging.getLogger(__name__)
# full screen mode
controller = Controller()
CV = Path.cwd() / 'cv_04_24.pdf'


class Job(BaseModel):
	title: str
	link: str
	company: str
	salary: Optional[str] = None
	location: Optional[str] = None


@controller.action('Save jobs to file', param_model=Job)
def save_jobs(job: Job):
	with open('jobs.csv', 'a', newline='') as f:
		writer = csv.writer(f)
		writer.writerow([job.title, job.company, job.link, job.salary, job.location])

	return 'Saved job to file'


@controller.action('Read jobs from file')
def read_jobs():
	with open('jobs.csv', 'r') as f:
		return f.read()


@controller.action('Read my cv for context to fill forms')
def read_cv():
	pdf = PdfReader(CV)
	text = ''
	for page in pdf.pages:
		text += page.extract_text() or ''
	logger.info(f'Read cv with {len(text)} characters')
	return ActionResult(extracted_content=text, include_in_memory=True)


@controller.action(
	'Upload cv to index - dont click the index - only call this function', requires_browser=True
)
async def upload_cv(index: int, browser: BrowserContext):
	page = await browser.get_current_page()
	path = str(CV.absolute())
	target_element = await browser.get_element_by_index(index)

	if not target_element:
		raise Exception(f'Could not find element at index {index}')

	async def attempt_1():
		is_visible = await target_element.is_visible()
		if not is_visible:
			return False

		# First check if element is a file input
		tag_name = await target_element.evaluate('el => el.tagName.toLowerCase()')
		if tag_name == 'input' and await target_element.evaluate("el => el.type === 'file'"):
			await target_element.set_input_files(path)
			return True

		return False

	async def attempt_2():
		# Direct input[type="file"] approach using the target element
		# Get all file inputs and find the one closest to our target element
		file_inputs = await page.query_selector_all('input[type="file"]')

		for input_element in file_inputs:
			# Check if this input is associated with our target element
			is_associated = await page.evaluate(
				"""
				([input, target]) => {
					const inputRect = input.getBoundingClientRect();
					const targetRect = target.getBoundingClientRect();
					const distance = Math.hypot(
						inputRect.left - targetRect.left,
						inputRect.top - targetRect.top
					);
					return distance < 200;
				}
			""",
				[input_element, target_element],
			)

			if is_associated:
				await input_element.set_input_files(path)
				return True
		return False

	for attempt_func in [attempt_1, attempt_2]:
		try:
			if await attempt_func():
				logger.info(f'Successfully uploaded file to index {index}')
				return f'Uploaded file to index {index}'

		except Exception as e:
			logger.debug(f'Error in {attempt_func.__name__}: {str(e)}')

	return ActionResult(error=f'Failed to upload file to index {index}')


browser = Browser(
	config=BrowserConfig(
		chrome_instance_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
	)
)


async def main():
	ground_task = (
		'You are a professional job finder and applyer. '
		'1. Read my cv with read_cv'
		'2. find machine learning internships which fit to my profile for the asked company'
		'3. Save all found internships to a file by calling save_jobs'
		'4. please avoid job portals like linkedin, indeed, etc., do everything you should do like uploading cv, motivation letter, etc, if you get stuck simply find a different job'
		'Rules: make sure to complete the the application, sometimes you need to scroll down or try a different approach'
		'Make sure to be on the english version of the page'
		'5. companies to search for it:'
	)
	tasks = [
		# ground_task + '\n' + 'Google',
		# ground_task + '\n' + 'Amazon',
		# ground_task
		'\n'
		# + 'Meta go to https://www.metacareers.com/resume/?req=a1KDp00000E2KF8MAN and upload cv with upload_cv',
		'go to https://kzmpmkh2zfk1ojnpxfn1.lite.vusercontent.net/ and upload my cv with upload_cv to each file uploader!',
		# ground_task + '\n' + 'Apple',
	]
	model = AzureChatOpenAI(
		model='gpt-4o',
		api_version='2024-10-21',
		azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT', ''),
		api_key=SecretStr(os.getenv('AZURE_OPENAI_KEY', '')),
	)

	agents = []
	for task in tasks:
		agent = Agent(task=task, llm=model, controller=controller, browser=browser)
		agents.append(agent)

	await asyncio.gather(*[agent.run() for agent in agents])


if __name__ == '__main__':
	asyncio.run(main())
