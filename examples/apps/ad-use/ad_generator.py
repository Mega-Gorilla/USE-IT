import argparse
import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def setup_environment(debug: bool):
	if not debug:
		os.environ['BROWSER_USE_SETUP_LOGGING'] = 'false'
		os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'critical'
		logging.getLogger().setLevel(logging.CRITICAL)
	else:
		os.environ['BROWSER_USE_SETUP_LOGGING'] = 'true'
		os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'info'


parser = argparse.ArgumentParser(description='Generate ads from landing pages using browser-use + 🍌')
parser.add_argument('url', nargs='?', help='Landing page URL to analyze')
parser.add_argument('--debug', action='store_true', default=False, help='Enable debug mode (show browser, verbose logs)')
args = parser.parse_args()
setup_environment(args.debug)

import aiofiles
from google import genai
from PIL import Image

from browser_use import Agent, BrowserSession
from browser_use.llm.google import ChatGoogle

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')


class LandingPageAnalyzer:
	def __init__(self, debug: bool = False):
		self.debug = debug
		self.llm = ChatGoogle(model='gemini-2.0-flash-exp', api_key=GOOGLE_API_KEY)
		self.output_dir = Path('output')
		self.output_dir.mkdir(exist_ok=True)

	async def analyze_landing_page(self, url: str) -> dict:
		browser_session = BrowserSession(
			headless=not self.debug,  # headless=False only when debug=True
			disable_security=True,
		)

		agent = Agent(
			task=f"""Go to {url} and quickly extract key brand information for Instagram ad creation.

Steps:
1. Navigate to the website
2. From the initial view, extract ONLY these essentials:
   - Brand/Product name
   - Main tagline or value proposition (one sentence)
   - Primary call-to-action text
   - Any visible pricing or special offer
3. Scroll down half a page, twice (0.5 pages each) to check for any key info
4. Done - keep it simple and focused on the brand

Return ONLY the key brand info, not page structure details.""",
			llm=self.llm,
			browser_session=browser_session,
			max_actions_per_step=2,
			step_timeout=30,
			use_thinking=False,
			vision_detail_level='high',
		)

		screenshot_path = None
		timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

		async def screenshot_callback(agent_instance):
			nonlocal screenshot_path
			import asyncio

			await asyncio.sleep(4)
			screenshot_path = self.output_dir / f'landing_page_{timestamp}.png'
			active_session = agent_instance.browser_session
			screenshot_data = await active_session.take_screenshot(path=str(screenshot_path), full_page=False)

		import asyncio

		screenshot_task = asyncio.create_task(screenshot_callback(agent))

		history = await agent.run()

		try:
			await screenshot_task
		except Exception as e:
			print(f'Screenshot task failed: {e}')

		analysis = history.final_result()
		if not analysis:
			analysis = 'No analysis content extracted'

		return {'url': url, 'analysis': analysis, 'screenshot_path': screenshot_path, 'timestamp': timestamp}


class AdGenerator:
	def __init__(self, api_key: str | None = GOOGLE_API_KEY):
		if not api_key:
			raise ValueError('GOOGLE_API_KEY is missing or empty – set the environment variable or pass api_key explicitly')

		self.client = genai.Client(api_key=api_key)
		self.output_dir = Path('output')
		self.output_dir.mkdir(exist_ok=True)

	def create_ad_prompt(self, browser_analysis: str) -> str:
		prompt = f"""Create an Instagram ad for this brand:

{browser_analysis}

Create a vibrant, eye-catching Instagram ad image with:
- Try to use the colors and style of the logo or brand, else:
- Bold, modern gradient background with bright colors
- Large, playful sans-serif text with the product/service name from the analysis
- Trendy design elements: geometric shapes, sparkles, emojis
- Fun bubbles or badges for any pricing or special offers mentioned
- Call-to-action button with text from the analysis
- Emphasizes the key value proposition from the analysis
- Uses visual elements that match the brand personality
- Square format (1:1 ratio)
- Use color psychology to drive action

Style: Modern Instagram advertisement, (1:1), scroll-stopping, professional but playful, conversion-focused"""
		return prompt

	async def generate_ad_image(self, prompt: str, screenshot_path: Path | None = None) -> bytes:
		"""Generate ad image bytes using Gemini. Returns *empty bytes* on failure."""

		try:
			from typing import Any

			contents: list[Any] = [prompt]

			if screenshot_path and screenshot_path.exists():
				screenshot_prompt = (
					'\n\nHere is the actual landing page screenshot to reference for design inspiration, '
					'colors, layout, and visual style:'
				)

				img = Image.open(screenshot_path)
				w, h = img.size
				side = min(w, h)
				img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))

				contents = [prompt + screenshot_prompt, img]

			response = self.client.models.generate_content(
				model='gemini-2.5-flash-image-preview',
				contents=contents,
			)

			cand = getattr(response, 'candidates', None)
			if cand:
				for part in getattr(cand[0].content, 'parts', []):
					inline = getattr(part, 'inline_data', None)
					if inline:
						return inline.data

		except Exception as e:
			print(f'❌ Image generation failed: {e}')

		return b''

	async def save_results(self, ad_image: bytes, prompt: str, analysis: str, url: str, timestamp: str) -> str:
		image_path = self.output_dir / f'ad_{timestamp}.png'
		async with aiofiles.open(image_path, 'wb') as f:
			await f.write(ad_image)

		analysis_path = self.output_dir / f'analysis_{timestamp}.txt'
		async with aiofiles.open(analysis_path, 'w', encoding='utf-8') as f:
			await f.write(f'URL: {url}\n\n')
			await f.write('BROWSER-USE ANALYSIS:\n')
			await f.write(analysis)
			await f.write('\n\nGENERATED PROMPT:\n')
			await f.write(prompt)

		return str(image_path)


def open_image(image_path: str):
	"""Open image with default system viewer"""
	try:
		if sys.platform.startswith('darwin'):
			# macOS
			subprocess.run(['open', image_path], check=True)
		elif sys.platform.startswith('win'):
			# Windows
			subprocess.run(['cmd', '/c', 'start', '', image_path], check=True)
		else:
			# Linux
			subprocess.run(['xdg-open', image_path], check=True)
	except Exception as e:
		print(f'❌ Could not open image: {e}')


async def create_ad_from_landing_page(url: str, debug: bool = False):
	analyzer = LandingPageAnalyzer(debug=debug)
	generator = AdGenerator()

	try:
		print(f'🚀 Analyzing {url}...')
		page_data = await analyzer.analyze_landing_page(url)

		prompt = generator.create_ad_prompt(page_data['analysis'])
		ad_image = await generator.generate_ad_image(prompt, page_data.get('screenshot_path'))
		result_path = await generator.save_results(ad_image, prompt, page_data['analysis'], url, page_data['timestamp'])

		print(f'🎨 Generated ad: {result_path}')
		if page_data.get('screenshot_path'):
			print(f'📸 Page screenshot: {page_data["screenshot_path"]}')
		open_image(result_path)

		return result_path

	except Exception as e:
		print(f'❌ Error: {e}')
		raise


if __name__ == '__main__':
	url = args.url
	if not url:
		url = input('🔗 Enter URL: ').strip() or 'https://www.apple.com/iphone-17-pro/'

	asyncio.run(create_ad_from_landing_page(url, debug=args.debug))
