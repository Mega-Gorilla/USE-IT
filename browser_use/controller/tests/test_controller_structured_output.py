import asyncio

from pydantic import BaseModel

from browser_use.agent.service import Agent
from browser_use.agent.views import AgentOutput
from browser_use.controller.service import Controller
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.schema import SchemaOptimizer


class OutputModel(BaseModel):
	"""Test output model"""

	name: str
	age: int


async def test_optimized_schema():
	"""Test the optimized schema generation and save to file."""

	# Create controller and get all registered actions
	controller = Controller()
	ActionModel = controller.registry.create_action_model()

	# Create the agent output model with custom actions
	agent_output_model = AgentOutput.type_with_custom_actions(ActionModel)

	# Get original schema for comparison
	original_schema = agent_output_model.model_json_schema()

	# Create the optimized schema
	optimized_schema = SchemaOptimizer.create_optimized_json_schema(agent_output_model)

	agent = Agent(
		task='What is the capital of France?',
		llm=ChatOpenAI(model='gpt-4o'),
		controller=controller,
	)

	output = await agent.run()
	x = output.structured_output


if __name__ == '__main__':
	asyncio.run(test_optimized_schema())
