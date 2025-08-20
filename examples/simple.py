import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from browser_use import Agent, default_llm


async def main():
	await Agent('Find the founders of browser-use', default_llm).run()


if __name__ == '__main__':
	asyncio.run(main())
