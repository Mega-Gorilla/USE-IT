import asyncio

from pydantic import BaseModel

from browser_use.agent.service import Agent
from browser_use.agent.utils import create_pydantic_model_from_schema
from browser_use.agent.views import AgentOutput
from browser_use.controller.service import Controller
from browser_use.llm.openai.chat import ChatOpenAI


class OutputModel(BaseModel):
	"""Test output model"""

	city: str
	country: str


async def test_optimized_schema():
	"""Test the optimized schema generation and save to file."""

	# Create controller and get all registered actions
	controller = Controller()
	ActionModel = controller.registry.create_action_model()

	# Create the agent output model with custom actions
	agent_output_model = AgentOutput.type_with_custom_actions(ActionModel)

	# # Get original schema for comparison
	# original_schema = agent_output_model.model_json_schema()

	# # Create the optimized schema
	# optimized_schema = SchemaOptimizer.create_optimized_json_schema(agent_output_model)

	scgena = create_pydantic_model_from_schema(OutputModel.model_json_schema(), 'OutputModel')

	agent = Agent(
		task='What is the capital of France? Do not use the internet, just output the done function.',
		llm=ChatOpenAI(model='gpt-4.1-mini'),
		controller=controller,
		output_model_schema=scgena,
	)

	history = await agent.run()

	if history.structured_output:
		# print(history.structured_output.city, history.structured_output.country)
		print(OutputModel.model_validate_json(history.final_result() or '{}'))
	else:
		print('No structured output')
		print(history.final_result())


if __name__ == '__main__':
	asyncio.run(test_optimized_schema())
