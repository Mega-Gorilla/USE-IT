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
task = 'Find a nice place to eat in San Francisco!'

agent = Agent(task=task, llm=llm, save_conversation_path='./conversation.json')


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
