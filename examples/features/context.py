import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from browser_use import Agent, Controller
from browser_use.agent.views import ActionResult

# Services -------------------------------------------------------------------


class InMemoryDatabase:
	def __init__(self):
		self.data = {}

	def get(self, key: str) -> str:
		print(f'Getting "{key}"!')
		return self.data[key]

	def set(self, key: str, value: str):
		print(f'Setting "{key}" to "{value}"!')
		self.data[key] = value


class Context:
	database: InMemoryDatabase


# Controller -----------------------------------------------------------------


class SaveToDatabaseAction(BaseModel):
	key: str
	value: str


class GetFromDatabaseAction(BaseModel):
	key: str


class CloudController(Controller):
	def __init__(self, exclude_actions: list[str], output_model: type[BaseModel] | None = None):
		super().__init__(exclude_actions, output_model)

		@self.registry.action(description='Save information to the database.', param_model=SaveToDatabaseAction)
		async def save_to_database(params: SaveToDatabaseAction, context: Context):
			context.database.set(params.key, params.value)

			return ActionResult(
				extracted_content=f'Saved {params.key} to the database.',
			)

		@self.registry.action(description='Get information from the database.', param_model=GetFromDatabaseAction)
		async def get_from_database(params: GetFromDatabaseAction, context: Context):
			value = context.database.get(params.key)

			return ActionResult(
				extracted_content=value,
			)


controller = CloudController(exclude_actions=[])

# Agent ----------------------------------------------------------------------

load_dotenv()

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4o',
	temperature=0.0,
)


async def main():
	task = """
1. Find the founders of browser-use.
2. Save founders information to database.
3. Draft them a short personalized message.
4. Save the message to the database!
5. Print that you did it!
    """.strip()

	context = Context()

	context.database = InMemoryDatabase()

	agent = Agent(task=task, llm=llm, controller=controller, context=context)

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
