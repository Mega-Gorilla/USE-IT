import json
import os
import sys
from datetime import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use import ActionResult, Agent, Controller

load_dotenv()

controller = Controller(exclude_actions=['search_google'])
BEARER_TOKEN = os.getenv('BEARER_TOKEN')

if not BEARER_TOKEN:
	# use the api key for ask tessa
	# you can also use other apis like exa, xAI, perplexity, etc.
	raise ValueError('BEARER_TOKEN is not set - go to https://www.heytessa.ai/ and create an api key')


@controller.registry.action('Search the web for a specific query')
async def search_web(query: str):
	keys_to_use = ['url', 'title', 'content', 'author', 'score']
	headers = {'Authorization': f'Bearer {BEARER_TOKEN}'}
	response = requests.post('https://asktessa.ai/api/search', headers=headers, json={'query': query})

	final_results = [
		{key: source[key] for key in keys_to_use if key in source}
		for source in response.json()['sources']
		if source['score'] >= 0.2
	]
	# print(json.dumps(final_results, indent=4))
	result_text = json.dumps(final_results, indent=4)
	return ActionResult(extracted_content=result_text, include_in_memory=True)


async def main():
	task = 'Find the contact email address of gregor zunic from browser-use'
	model = ChatOpenAI(model='gpt-4o')
	agent = Agent(task=task, llm=model, controller=controller)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
