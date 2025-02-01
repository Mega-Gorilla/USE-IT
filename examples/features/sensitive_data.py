import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent

load_dotenv()

# Initialize the model
llm = ChatOpenAI(
	model='gpt-4o',
	temperature=0.0,
)
# the llm will never see the values of your sensitve data, it will only see the keys. Internally we replace after the llm the keys with the values.
sensitive_data = {'x_name': 'mamagnus00', 'x_password': '12345678'}
task = 'go to x and login with x_name and x_password then find interesting posts and like them'

agent = Agent(task=task, llm=llm, sensitive_data=sensitive_data)


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
