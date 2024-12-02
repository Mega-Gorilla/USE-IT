"""
Find and apply to jobs.

@dev You need to add OPENAI_API_KEY to your environment variables.

Also you have to install PyPDF2 to read pdf files: pip install PyPDF2
"""

import csv
import os
import sys
from pathlib import Path

from PyPDF2 import PdfReader

from browser_use.browser.service import Browser

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from typing import List, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use import ActionResult, Agent, BrowserConfig, Controller

load_dotenv()

controller = Controller(browser_config=BrowserConfig(keep_open=True, disable_security=True))
CV = Path.cwd() / 'cv_04_24.pdf'


class Job(BaseModel):
	title: str
	link: str
	company: str
	salary: Optional[str] = None
	location: Optional[str] = None


class Jobs(BaseModel):
	jobs: List[Job]


@controller.action('Save jobs to file', param_model=Jobs)
def save_jobs(params: Jobs):
	with open('jobs.csv', 'a', newline='') as f:
		writer = csv.writer(f)
		for job in params.jobs:
			writer.writerow([job.title, job.company, job.link, job.salary, job.location])


@controller.action('Read jobs from file')
def read_jobs():
	with open('jobs.csv', 'r') as f:
		return f.read()


# @controller.action('Ask me for help')
# def ask_human(question: str) -> str:
# 	return input(f'\n{question}\nInput: ')


@controller.action('Read my cv for context to fill forms')
def read_cv():
	pdf = PdfReader(CV)
	text = ''
	for page in pdf.pages:
		text += page.extract_text() or ''
	return ActionResult(extracted_content=text, include_in_memory=True)


@controller.action('Upload cv to index', requires_browser=True)
async def upload_cv(index: int, browser: Browser):
	await close_file_dialog(browser)
	element = await browser.get_element_by_index(index)
	if not element:
		raise Exception(f'Element with index {index} not found')

	await element.set_input_files(str(CV.absolute()))
	return f'Uploaded cv to index {index}'


@controller.action('Close file dialog', requires_browser=True)
async def close_file_dialog(browser: Browser):
	page = await browser.get_current_page()
	await page.keyboard.press('Escape')


async def main():
	task = (
		'You are a professional job finder and applyer. '
		'Read my cv & find 10 machine learning engineer jobs in Bangalore for me + apply me to them. '
		'Save them to a file'
		'use multiple tabs'
		'please avoid job portals like linkedin, indeed, etc. '
		'do everything you should do like uploading cv, motivation letter, etc. '
		'if you get stuck simply find a new job '
		'start with https://www.inmobi.com/company/openings/fte/jobid/6115486?utm_campaign=google_jobs_apply&utm_source=google_jobs_apply&utm_medium=organic'
	)
	model = ChatOpenAI(model='gpt-4o')
	agent = Agent(task=task, llm=model, controller=controller)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
