"""
Example command:

uv run python example.py --session_name mem-docs-agent  --session_id 28032025_001 --memory_interval 10
"""

import argparse
import asyncio
import json
import os
from typing import List

from dotenv import load_dotenv

load_dotenv()


from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use import Agent, Browser, BrowserConfig, Controller

links = [
	'https://docs.mem0.ai/components/llms/models/litellm',
	'https://docs.mem0.ai/components/llms/models/mistral_AI',
	'https://docs.mem0.ai/components/llms/models/ollama',
	'https://docs.mem0.ai/components/llms/models/openai',
	'https://docs.mem0.ai/components/llms/models/together',
	'https://docs.mem0.ai/components/llms/models/xAI',
	'https://docs.mem0.ai/components/llms/overview',
	'https://docs.mem0.ai/components/vectordbs/config',
	'https://docs.mem0.ai/components/vectordbs/dbs/azure_ai_search',
	'https://docs.mem0.ai/components/vectordbs/dbs/chroma',
	'https://docs.mem0.ai/components/vectordbs/dbs/elasticsearch',
	'https://docs.mem0.ai/components/vectordbs/dbs/milvus',
	'https://docs.mem0.ai/components/vectordbs/dbs/opensearch',
	'https://docs.mem0.ai/components/vectordbs/dbs/pgvector',
	'https://docs.mem0.ai/components/vectordbs/dbs/pinecone',
	'https://docs.mem0.ai/components/vectordbs/dbs/qdrant',
	'https://docs.mem0.ai/components/vectordbs/dbs/redis',
	'https://docs.mem0.ai/components/vectordbs/dbs/supabase',
	'https://docs.mem0.ai/components/vectordbs/dbs/vertex_ai_vector_search',
	'https://docs.mem0.ai/components/vectordbs/dbs/weaviate',
	'https://docs.mem0.ai/components/vectordbs/overview',
	'https://docs.mem0.ai/contributing/development',
	'https://docs.mem0.ai/contributing/documentation',
	'https://docs.mem0.ai/core-concepts/memory-operations',
	'https://docs.mem0.ai/core-concepts/memory-types',
	'https://docs.mem0.ai/examples/ai_companion_js',
	'https://docs.mem0.ai/examples/chrome-extension',
	'https://docs.mem0.ai/examples/customer-support-agent',
	'https://docs.mem0.ai/examples/document-writing',
	'https://docs.mem0.ai/examples/llama-index-mem0',
	'https://docs.mem0.ai/examples/mem0-agentic-tool',
	'https://docs.mem0.ai/examples/mem0-demo',
	'https://docs.mem0.ai/examples/mem0-openai-voice-demo',
	'https://docs.mem0.ai/examples/mem0-with-ollama',
	'https://docs.mem0.ai/examples/multimodal-demo',
	'https://docs.mem0.ai/examples/openai-inbuilt-tools',
	'https://docs.mem0.ai/examples/overview',
	'https://docs.mem0.ai/examples/personal-ai-tutor',
	'https://docs.mem0.ai/examples/personal-travel-assistant',
	'https://docs.mem0.ai/examples/personalized-deep-research',
	'https://docs.mem0.ai/faqs',
	'https://docs.mem0.ai/features/advanced-retrieval',
	'https://docs.mem0.ai/features/async-client',
	'https://docs.mem0.ai/features/contextual-add',
	'https://docs.mem0.ai/features/custom-categories',
	'https://docs.mem0.ai/features/custom-fact-extraction-prompt',
	'https://docs.mem0.ai/features/custom-instructions',
	'https://docs.mem0.ai/features/custom-update-memory-prompt',
	'https://docs.mem0.ai/features/direct-import',
	'https://docs.mem0.ai/features/feedback-mechanism',
]

print('-' * 100)
print(f'Total links: {len(links)}')
print('-' * 100)


class Link(BaseModel):
	url: str
	title: str
	summary: str


class Links(BaseModel):
	links: List[Link]


initial_actions = [
	{'open_tab': {'url': 'https://docs.mem0.ai/'}},
]
controller = Controller(output_model=Links)


async def main(session_name, session_id, max_steps, memory_interval):
	config = BrowserConfig(headless=True)
	browser = Browser(config=config)

	agent = Agent(
		task=f"""Visit all the links provided in {links} and summarize the content of each page with url and title. There are {len(links)} links to visit. The summary should be SEO friendly based on the title and content of the page. The pages you are visiting are the documentation of Mem0. Return a json with the following format: [{{url: <url>, title: <title>, summary: <summary>}}]. 

        Guidelines:
        1. Strictly stay on the domain https://docs.mem0.ai
        2. Do not visit any other websites
        3. Ignore the links that are hashed (#) or javascript (:), or mailto, or tel, or other protocols
        4. Capture the unique urls which are not already visited.
        5. If you visit any page that doesn't have host name docs.mem0.ai, then do not visit it and come back to the page with host name docs.mem0.ai.
        """,
		llm=ChatOpenAI(
			model='gpt-4o-mini',
			model_kwargs={
				'extra_headers': {
					'Helicone-Auth': f'Bearer {os.environ["HELICONE_API_KEY"]}',
					'Helicone-Session-Id': session_id,
					'Helicone-Session-Name': session_name,
				},
			},
			base_url='https://oai.helicone.ai/v1',
		),
		controller=controller,
		initial_actions=initial_actions,
		enable_memory=True if memory_interval > 0 else False,
		memory_interval=memory_interval,
		browser=browser,
	)
	history = await agent.run(max_steps=max_steps)
	result = history.final_result()
	parsed_result = []
	if result:
		parsed: Links = Links.model_validate_json(result)
		print('-- RESULTS --')
		print(f'Total links: {len(parsed.links)}')
		for link in parsed.links:
			parsed_result.append({'title': link.title, 'url': link.url, 'summary': link.summary})
	else:
		print('No result')

	# Write to a file
	with open(f'result_{session_name}_{session_id}.json', 'w+') as f:
		f.write(json.dumps(parsed_result, indent=4))


if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument('--session_name', type=str, required=True)
	parser.add_argument('--session_id', type=str, required=True)
	parser.add_argument('--max_steps', type=int, default=500)
	parser.add_argument('--memory_interval', type=int, default=-1)
	args = parser.parse_args()
	session_name = args.session_name
	session_id = args.session_id
	max_steps = args.max_steps
	memory_interval = args.memory_interval
	print('--- Arguments Start ---')
	print(f'Session Name: {session_name}')
	print(f'Session ID: {session_id}')
	print(f'Max Steps: {max_steps}')
	print(f'Memory Interval: {memory_interval}')
	print('--- Arguments End ---')
	asyncio.run(main(session_name, session_id, max_steps, memory_interval))
