"""
This script creates a Gradio interface to run browser tasks using OpenAI models.
By default, it uses regular OpenAI. To use Azure OpenAI, set the AZURE_ENDPOINT environment variable:

- AZURE_ENDPOINT: Your Azure OpenAI endpoint URL

The script will automatically detect this variable and switch to using Azure OpenAI if it is set.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import List, Optional

import gradio as gr
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from browser_use import Agent

# Conditionally import AzureChatOpenAI or ChatOpenAI based on the presence of the AZURE_ENDPOINT environment variable
if os.getenv("AZURE_ENDPOINT"):
    from langchain_openai import AzureChatOpenAI as ChatOpenAI  # Use Azure if environment variable is set
else:
    from langchain_openai import ChatOpenAI  # Use regular OpenAI otherwise

from browser_use import Agent

load_dotenv()


@dataclass
class ActionResult:
	is_done: bool
	extracted_content: Optional[str]
	error: Optional[str]
	include_in_memory: bool


@dataclass
class AgentHistoryList:
	all_results: List[ActionResult]
	all_model_outputs: List[dict]


def parse_agent_history(history_str: str) -> None:
	console = Console()

	# Split the content into sections based on ActionResult entries
	sections = history_str.split('ActionResult(')

	for i, section in enumerate(sections[1:], 1):  # Skip first empty section
		# Extract relevant information
		content = ''
		if 'extracted_content=' in section:
			content = section.split('extracted_content=')[1].split(',')[0].strip("'")

		if content:
			header = Text(f'Step {i}', style='bold blue')
			panel = Panel(content, title=header, border_style='blue')
			console.print(panel)
			console.print()


async def run_browser_task(
    task: str,
    api_key: str,
    model: str = 'gpt-4',
    headless: bool = True,
) -> str:
    if not api_key.strip():
        return 'Please provide an API key'

    azure_endpoint = os.getenv("AZURE_ENDPOINT")

    if azure_endpoint:
        os.environ['AZURE_API_KEY'] = api_key
        os.environ['AZURE_ENDPOINT'] = azure_endpoint
        os.environ['OPENAI_API_VERSION'] = '2024-08-01-preview'  # Set the API version globally for Azure OpenAI

        try:
            agent = Agent(
                task=task,
                llm=ChatOpenAI(
                    model_name=model,
                    openai_api_key=api_key,
                    azure_endpoint=azure_endpoint,
                    deployment_name=model,
                    api_version='2024-08-01-preview'  # Explicitly set the API version
                ),
            )
        except Exception as e:
            return f'Error: {str(e)}'
    else:
        os.environ['OPENAI_API_KEY'] = api_key

        try:
            agent = Agent(
                task=task,
                llm=ChatOpenAI(model=model),
            )
        except Exception as e:
            return f'Error: {str(e)}'

    result = await agent.run()
    return result
	


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
				model = gr.Dropdown(
					choices=['gpt-4o', 'gpt-4', 'gpt-3.5-turbo'],  # Added gpt-4o
				)
				headless = gr.Checkbox(label='Run Headless', value=True)
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
