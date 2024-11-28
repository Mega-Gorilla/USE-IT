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
from browser_use.dom.views import SelectorMap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio

from langchain_openai import ChatOpenAI

from browser_use import ActionModel, Agent, AgentHistoryList, Controller
from browser_use.agent.views import AgentOutput

llm = ChatOpenAI(model='gpt-4o-mini')
controller = Controller(keep_open=False)


@controller.registry.action(description='Prints the secret text')
async def secret_text(secret_text: str) -> None:
	print(secret_text)


agent = Agent(
	# task='Find flights on kayak.com from Zurich to Beijing on 25.12.2024 to 02.02.2025',
	task='Search for elon musk on google and click the first result scroll down',
	llm=llm,
	controller=controller,
)


async def main():
	history: AgentHistoryList = await agent.run(2)
	# agent.save_history(file)
	await controller.browser.close(force=True)

	history.save_to_file('AgentHistoryList.json')

	print(f'history: {history}')

	await rerun_task()


async def rerun_task():
	# create new controller and agent
	controller2 = Controller(keep_open=False)
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

	for i, history_item in enumerate(history2.history):
		print(f'history_item {i}')
		ouput = history_item.model_output
		if ouput and ouput.action:
			try:
				# check if state is the same as previous
				prev_selector_map: SelectorMap = history_item.state.selector_map
				state = await controller2.browser.get_state()
				current_selector_map: SelectorMap = state.selector_map
				# check which values are different
				diff = [
					v1
					for v1, v2 in zip(prev_selector_map.values(), current_selector_map.values())
					if v1 != v2
				]
				# keys unique difference
				diff = prev_selector_map.keys() - current_selector_map.keys()
				if diff:
					print(f'diff: {diff}')
					continue

				# get exclude unset=True field in action
				action = ouput.action.model_dump(exclude_unset=True)
				# get index of clicked if index  param in output.action  e.g. click_element(index=0), input(index=0, value='test'),
				# check if index is in param of action {'click_element': {'index': 9, 'num_clicks': 1}}
				params = dict(action.values())
				index_exists = 'index' in params
				if index_exists:
					index = params['index']
					# get element with index from prev selector map
					prev_element = prev_selector_map[index]
					# get element with index from current selector map
					current_element = current_selector_map[index]
					if prev_element != current_element:
						print(f'element changed: {prev_element} -> {current_element}')
						continue
				action_model = await controller2.act(action)
			except Exception as e:
				print(f'Error executing action {ouput.action}: {e}')

			# wait
			await asyncio.sleep(2)
	print(f'done')


asyncio.run(main())
