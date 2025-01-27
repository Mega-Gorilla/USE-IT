import asyncio
import os
from dataclasses import dataclass
from typing import List, Optional

import gradio as gr
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI  # Updated import
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

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
    endpoint: str,
    model: str = 'gpt-4o',
    headless: bool = True,
) -> str:
    if not api_key.strip() or not endpoint.strip():
        return 'Please provide both API key and endpoint'

    # Set environment variables for Azure
    os.environ['AZURE_API_KEY'] = api_key
    os.environ['AZURE_ENDPOINT'] = endpoint
    os.environ['OPENAI_API_VERSION'] = '2024-08-01-preview'  # Set the API version globally

    try:
        agent = Agent(
            task=task,
            llm=AzureChatOpenAI(
                model_name=model, 
                openai_api_key=api_key,
                azure_endpoint=endpoint,  # Corrected to use azure_endpoint instead of openai_api_base
                deployment_name=model,  # Use deployment_name for Azure models
                api_version='2024-08-01-preview'  # Explicitly set the API version here
            ),
        )
        result = await agent.run()
        # TODO: The result could be parsed better
        return result
    except Exception as e:
        return f'Error: {str(e)}'


def create_ui():
    with gr.Blocks(title='Browser Use GUI') as interface:
        gr.Markdown('# Browser Use Task Automation')

        with gr.Row():
            with gr.Column():
                api_key = gr.Textbox(label='Azure OpenAI API Key', placeholder='sk-...', type='password')
                endpoint = gr.Textbox(label='Azure Endpoint', placeholder='https://<your-endpoint>.openai.azure.com/', type='text')
                task = gr.Textbox(
                    label='Task Description',
                    placeholder='E.g., Find flights from New York to London for next week',
                    lines=3,
                )
                model = gr.Dropdown(
                    choices=['gpt-4o', 'o1', 'gpt-4', 'gpt-3.5-turbo'],  # Added gpt-4o and o1
                    label='Model', 
                    value='gpt-4'  # Default value can be gpt-4 or any other
                )
                headless = gr.Checkbox(label='Run Headless', value=True)
                submit_btn = gr.Button('Run Task')

            with gr.Column():
                output = gr.Textbox(label='Output', lines=10, interactive=False)

        submit_btn.click(
            fn=lambda *args: asyncio.run(run_browser_task(*args)),
            inputs=[task, api_key, endpoint, model, headless],
            outputs=output,
        )

    return interface


if __name__ == '__main__':
    demo = create_ui()
    demo.launch()
