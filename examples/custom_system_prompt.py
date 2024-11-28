import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from langchain_openai import ChatOpenAI

from browser_use import Agent, SystemPrompt


class MySystemPrompt(SystemPrompt):
	def important_rules(self) -> str:
		existing_rules = super().important_rules()
		new_rules = 'ALWAYS go first to url wikipedia.com no matter the task'
		return f'{existing_rules}\n{new_rules}'

		# other methods can be overriden as well (not recommended)
		# example_response -> str
		# input_format -> str
		# response_format -> str
		# get_system_message -> SystemMessage


async def main():
	task = 'do google search to find images of elon musk wife'
	model = ChatOpenAI(model='gpt-4o')
	agent = Agent(task=task, llm=model, system_prompt_class=MySystemPrompt)

	print(
		json.dumps(
			agent.message_manager.system_prompt.model_dump(exclude_unset=True),
			indent=4,
		)
	)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
