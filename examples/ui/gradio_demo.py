# pyright: reportMissingImports=false
import asyncio
import os
import sys
from dataclasses import dataclass

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))



# Third-party imports
import gradio as gr  # type: ignore

# Local module imports
from browser_use import Agent, ChatOpenAI


@dataclass
class ActionResult:
	is_done: bool
	extracted_content: str | None
	error: str | None
	include_in_memory: bool


@dataclass
class AgentHistoryList:
	all_results: list[ActionResult]
	all_model_outputs: list[dict]


async def run_browser_task(
	task: str,
	api_key: str,
	model: str = 'gpt-4.1',
	headless: bool = True,
) -> str:
	if not api_key.strip():
		return 'Please provide an API key'

	os.environ['OPENAI_API_KEY'] = api_key

	try:
		agent = Agent(
			task=task,
			llm=ChatOpenAI(model='gpt-4.1-mini'),
		)
		result = await agent.run()
		#  TODO: The result could be parsed better
		return str(result)
	except Exception as e:
		return f'Error: {str(e)}'


def create_ui():
	with gr.Blocks(title='Browser Use GUI') as interface:
		gr.Markdown('# Browser Use Task Automation')

		with gr.Row():
			with gr.Column():
				api_key = gr.Textbox(label='OpenAI API Key', placeholder='sk-...', type='password')
				task = gr.Textbox(
					label='Task Description',
					placeholder='E.g., Find flights from New York to London for next week',
					lines=3,
				)
				model = gr.Dropdown(choices=['gpt-4.1-mini', 'gpt-5', 'o3', 'gpt-5-mini'], label='Model', value='gpt-4.1-mini')
				headless = gr.Checkbox(label='Run Headless', value=False)
				submit_btn = gr.Button('Run Task')

			with gr.Column():
				output = gr.Textbox(label='Output', lines=10, interactive=False)

		submit_btn.click(
			fn=lambda *args: asyncio.run(run_browser_task(*args)),
			inputs=[task, api_key, model, headless],
			outputs=output,
		)

	return interface


if __name__ == '__main__':
	demo = create_ui()
	demo.launch()
