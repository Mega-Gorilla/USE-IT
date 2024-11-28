"""
Simple try of the agent.

@dev You need to add OPENAI_API_KEY to your environment variables.
"""

import json
import os
import sys
from doctest import OutputChecker
from pprint import pprint

from browser_use.agent.views import AgentBrain
from browser_use.dom.history_tree_processor import HistoryTreeProcessor
from browser_use.dom.views import DOMElementNode, SelectorMap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI

from browser_use import ActionModel, Agent, AgentHistoryList, Controller
from browser_use.agent.views import AgentOutput

llm = ChatOpenAI(model='gpt-4o')
controller = Controller(keep_open=False, cookies_path='cookies.json')


@controller.registry.action(description='Prints the secret text')
async def secret_text(secret_text: str) -> None:
	print(secret_text)


agent = Agent(
	# task='Go to kayak.com and search for flights from Zurich to Beijing and then done.',
	task='Find flights on kayak.com from Zurich to Beijing on 25.12.2024 to 02.02.2025',
	# task='Search for elon musk on google and click the first result scroll down',
	llm=llm,
	controller=controller,
)


async def main():
	history: AgentHistoryList = await agent.run(5)
	# agent.save_history(file)
	await controller.browser.close(force=True)

	history.save_to_file('AgentHistoryList.json')

	await rerun_task()


async def rerun_task():
	# create new controller and agent
	controller2 = Controller(keep_open=False, cookies_path='cookies.json')
	agent2 = Agent(
		task='',
		llm=llm,
		controller=controller2,
	)
	output_model = agent2.AgentOutput

	history2 = AgentHistoryList.load_from_file('AgentHistoryList.json', output_model)

	# pydantic model for actions

	print(f'rerun task')
	actions = history2.action_names()
	outputs = history2.model_outputs()
	print(actions)

	# close controller
	# get all interacted elements
	interacted_elements = [h.state.interacted_element for h in history2.history]
	for i, history_item in enumerate(history2.history):
		print(f'{i} {actions[i]} ')
		ouput = history_item.model_output
		if ouput and ouput.action:
			goal = ouput.current_state.next_goal
			print(f'goal: {goal}')
			try:
				# check if state is the same as previous
				old_el = history_item.state.interacted_element
				state = await controller2.browser.get_state()
				tree = state.element_tree

				if old_el and tree:
					element: DOMElementNode | None = (
						HistoryTreeProcessor.find_history_element_in_tree(old_el, tree)
					)
					if element:
						index = element.highlight_index
						old_index = ouput.action.get_index()
						print(f'same element found with index: {index} and old index: {old_index}')

						if old_index != index and index is not None:
							ouput.action.set_index(index)
					else:
						print(f'old element not found in new tree')
						continue

				action_result = await controller2.act(ouput.action)
				if action_result.error:
					print(f'Step {i} failed: {action_result.error}')
				else:
					print(f'Step {i} succeeded: {action_result.extracted_content}')
			except Exception as e:
				print(f'Error executing action {ouput.action}: {e}')

			# wait
			await asyncio.sleep(2)
	print(f'done')


asyncio.run(main())
