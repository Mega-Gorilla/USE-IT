import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from typing import List, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use.agent.service import Agent
from browser_use.browser.browser import Browser
from browser_use.controller.service import Controller

# Initialize controller first
controller = Controller()


# Action Models
class JobDetails(BaseModel):
	title: str
	company: str
	job_link: str
	salary: Optional[str] = None


@controller.action('Save job details which you found on page', param_model=JobDetails)
def save_job(params: JobDetails):
	with open('jobs.txt', 'a') as f:
		f.write(f'{params.title} at {params.company}: {params.salary}\n')


class StarredPeople(BaseModel):
	usernames: List[str]


@controller.action('Save people who starred the repo', param_model=StarredPeople)
def save_starred_people(params: StarredPeople):
	with open('starred_people.txt', 'a') as f:
		for username in params.usernames:
			f.write(f'{username}\n')


# Browser-requiring action example
class PageSaver(BaseModel):
	filename: str


@controller.action('Save current page info', param_model=PageSaver, requires_browser=True)
async def save_page_info(params: PageSaver, browser: Browser):
	session = await browser.get_session()
	state = session.cached_state
	with open(params.filename, 'w') as f:
		f.write(f'URL: {state.url}\n')
		f.write(f'Title: {state.title}\n')
		# f.write(f'HTML: {state.element_tree}\n')


class Job(BaseModel):
	title: str
	link: str
	company: str
	salary: Optional[str] = None


class Jobs(BaseModel):
	jobs: List[Job]


@controller.action('Save jobs', param_model=Jobs)
def save_jobs(params: Jobs):
	with open('jobs.txt', 'a') as f:
		for job in params.jobs:
			f.write(f'{job.title} at {job.company}: {job.salary} ({job.link})\n')


# Without Pydantic model - using simple parameters
@controller.action('Ask user for information')
def ask_human(question: str) -> str:
	return input(f'\n{question}\nInput: ')


async def main():
	task = 'Find 10 software developer jobs in San Francisco at YC startups in google and save the jobs to a file. Then ask human for more information'

	model = ChatOpenAI(model='gpt-4o')
	agent = Agent(task=task, llm=model, controller=controller)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
