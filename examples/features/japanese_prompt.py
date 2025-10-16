import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()


from browser_use import Agent, ChatGoogle


async def main():
	task = 'browser-use の GitHub リポジトリで最新の更新内容を調べ、要点を報告してください。'
	model = ChatGoogle(model='gemini-flash-latest')

	agent = Agent(
		task=task,
		llm=model,
		language='jp',  # 日本語版システムプロンプトを使用
	)

	system_prompt = agent.message_manager.system_prompt.content
	print('--- System Prompt Preview (first 500 chars) ---')
	print(system_prompt[:500])
	print('-----------------------------------------------')

	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())
