"""
Advanced example showing Playwright and Browser-Use working together with custom actions.

This example demonstrates:
1. Starting Chrome with CDP (Chrome DevTools Protocol) enabled
2. Creating custom actions that use Playwright functions
3. Using Browser-Use AI to orchestrate the overall workflow
4. Both tools sharing the same browser session seamlessly

Dependencies: playwright, aiohttp, browser-use
Run: python examples/integrations/playwright_browser_use_shared_cdp.py
"""

import asyncio
import os
import subprocess
import sys
import tempfile

from pydantic import BaseModel, Field


# Check for required dependencies
def check_dependencies():
	"""Check if required packages are installed."""
	missing_deps = []

	try:
		__import__('playwright')
	except ImportError:
		missing_deps.append('playwright')

	try:
		__import__('aiohttp')
	except ImportError:
		missing_deps.append('aiohttp')

	if missing_deps:
		print(f'❌ Missing dependencies: {", ".join(missing_deps)}')
		print('Install with: uv add ' + ' '.join(missing_deps))
		print('Also run: playwright install chromium')
		sys.exit(1)

	pass  # Dependencies found


# Import after dependency check
check_dependencies()
import aiohttp
from playwright.async_api import Browser, Page, async_playwright

from browser_use import Agent, BrowserSession, ChatOpenAI, Tools
from browser_use.agent.views import ActionResult

# Global Playwright browser instance - shared between custom actions
playwright_browser: Browser | None = None
playwright_page: Page | None = None


# Custom action parameter models
class PlaywrightFillFormAction(BaseModel):
	"""Parameters for Playwright form filling action."""

	customer_name: str = Field(..., description='Customer name to fill')
	phone_number: str = Field(..., description='Phone number to fill')
	email: str = Field(..., description='Email address to fill')
	size_option: str = Field(..., description='Size option (small/medium/large)')


class PlaywrightScreenshotAction(BaseModel):
	"""Parameters for Playwright screenshot action."""

	filename: str = Field(default='playwright_screenshot.png', description='Filename for screenshot')
	quality: int | None = Field(default=None, description='JPEG quality (1-100), only for .jpg/.jpeg files')


class PlaywrightGetTextAction(BaseModel):
	"""Parameters for getting text using Playwright selectors."""

	selector: str = Field(..., description='CSS selector to get text from')


async def start_chrome_with_debug_port(port: int = 9222):
	"""
	Start Chrome with remote debugging enabled.
	Returns the Chrome process.
	"""
	# Create temporary directory for Chrome user data
	user_data_dir = tempfile.mkdtemp(prefix='chrome_cdp_')

	# Chrome launch command
	chrome_paths = [
		'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',  # macOS
		'/usr/bin/google-chrome',  # Linux
		'/usr/bin/chromium-browser',  # Linux Chromium
		'chrome',  # Windows/PATH
		'chromium',  # Generic
	]

	chrome_exe = None
	for path in chrome_paths:
		if os.path.exists(path) or path in ['chrome', 'chromium']:
			try:
				# Test if executable works
				test_proc = await asyncio.create_subprocess_exec(
					path, '--version', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
				)
				await test_proc.wait()
				chrome_exe = path
				break
			except Exception:
				continue

	if not chrome_exe:
		raise RuntimeError('❌ Chrome not found. Please install Chrome or Chromium.')

	# Chrome command arguments
	cmd = [
		chrome_exe,
		f'--remote-debugging-port={port}',
		f'--user-data-dir={user_data_dir}',
		'--no-first-run',
		'--no-default-browser-check',
		'--disable-extensions',
		'about:blank',  # Start with blank page
	]

	# Start Chrome process
	process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

	# Wait for Chrome to start and CDP to be ready
	cdp_ready = False
	for _ in range(20):  # 20 second timeout
		try:
			async with aiohttp.ClientSession() as session:
				async with session.get(
					f'http://localhost:{port}/json/version', timeout=aiohttp.ClientTimeout(total=1)
				) as response:
					if response.status == 200:
						cdp_ready = True
						break
		except Exception:
			pass
		await asyncio.sleep(1)

	if not cdp_ready:
		process.terminate()
		raise RuntimeError('❌ Chrome failed to start with CDP')

	return process


async def connect_playwright_to_cdp(cdp_url: str):
	"""
	Connect Playwright to the same Chrome instance Browser-Use is using.
	This enables custom actions to use Playwright functions.
	"""
	global playwright_browser, playwright_page

	playwright = await async_playwright().start()
	playwright_browser = await playwright.chromium.connect_over_cdp(cdp_url)

	# Get or create a page
	if playwright_browser.contexts and playwright_browser.contexts[0].pages:
		playwright_page = playwright_browser.contexts[0].pages[0]
	else:
		context = await playwright_browser.new_context()
		playwright_page = await context.new_page()


# Create custom tools that use Playwright functions
tools = Tools()


@tools.registry.action(
	"Fill out a form using Playwright's precise form filling capabilities. This uses Playwright selectors for reliable form interaction.",
	param_model=PlaywrightFillFormAction,
)
async def playwright_fill_form(params: PlaywrightFillFormAction, browser_session: BrowserSession):
	"""
	Custom action that uses Playwright to fill forms with high precision.
	This demonstrates how to create Browser-Use actions that leverage Playwright's capabilities.
	"""
	try:
		if not playwright_page:
			return ActionResult(error='Playwright not connected. Run setup first.')

		# Filling form with Playwright's precise selectors

		# Use Playwright's robust selectors to fill the form
		await playwright_page.fill('input[name="custname"]', params.customer_name)
		await playwright_page.fill('input[name="custtel"]', params.phone_number)
		await playwright_page.fill('input[name="custemail"]', params.email)
		await playwright_page.select_option('select[name="size"]', params.size_option)

		# Get form data to verify it was filled
		form_data = {}
		form_data['name'] = await playwright_page.input_value('input[name="custname"]')
		form_data['phone'] = await playwright_page.input_value('input[name="custtel"]')
		form_data['email'] = await playwright_page.input_value('input[name="custemail"]')
		form_data['size'] = await playwright_page.input_value('select[name="size"]')

		success_msg = f'✅ Form filled successfully with Playwright: {form_data}'

		return ActionResult(
			extracted_content=success_msg, include_in_memory=True, long_term_memory=f'Filled form with: {form_data}'
		)

	except Exception as e:
		error_msg = f'❌ Playwright form filling failed: {str(e)}'
		return ActionResult(error=error_msg)


@tools.registry.action(
	"Take a screenshot using Playwright's screenshot capabilities with high quality and precision.",
	param_model=PlaywrightScreenshotAction,
)
async def playwright_screenshot(params: PlaywrightScreenshotAction, browser_session: BrowserSession):
	"""
	Custom action that uses Playwright's advanced screenshot features.
	"""
	try:
		if not playwright_page:
			return ActionResult(error='Playwright not connected. Run setup first.')

		# Taking screenshot with Playwright

		# Use Playwright's screenshot with full page capture
		screenshot_kwargs = {'path': params.filename, 'full_page': True}

		# Add quality parameter only for JPEG files
		if params.quality is not None and params.filename.lower().endswith(('.jpg', '.jpeg')):
			screenshot_kwargs['quality'] = params.quality

		await playwright_page.screenshot(**screenshot_kwargs)

		success_msg = f'✅ Screenshot saved as {params.filename} using Playwright'

		return ActionResult(
			extracted_content=success_msg, include_in_memory=True, long_term_memory=f'Screenshot saved: {params.filename}'
		)

	except Exception as e:
		error_msg = f'❌ Playwright screenshot failed: {str(e)}'
		return ActionResult(error=error_msg)


@tools.registry.action(
	"Extract text from elements using Playwright's powerful CSS selectors and XPath support.", param_model=PlaywrightGetTextAction
)
async def playwright_get_text(params: PlaywrightGetTextAction, browser_session: BrowserSession):
	"""
	Custom action that uses Playwright's advanced text extraction with CSS selectors and XPath.
	"""
	try:
		if not playwright_page:
			return ActionResult(error='Playwright not connected. Run setup first.')

		# Extracting text with Playwright selectors

		# Use Playwright's robust element selection and text extraction
		element = playwright_page.locator(params.selector).first

		if await element.count() == 0:
			error_msg = f'❌ No element found with selector: {params.selector}'
			return ActionResult(error=error_msg)

		text_content = await element.text_content()
		inner_text = await element.inner_text()

		# Get additional element info
		tag_name = await element.evaluate('el => el.tagName')
		is_visible = await element.is_visible()

		result_data = {
			'selector': params.selector,
			'text_content': text_content,
			'inner_text': inner_text,
			'tag_name': tag_name,
			'is_visible': is_visible,
		}

		success_msg = f'✅ Extracted text using Playwright: {result_data}'

		return ActionResult(
			extracted_content=str(result_data),
			include_in_memory=True,
			long_term_memory=f'Extracted from {params.selector}: {text_content}',
		)

	except Exception as e:
		error_msg = f'❌ Playwright text extraction failed: {str(e)}'
		return ActionResult(error=error_msg)


async def main():
	"""
	Main function demonstrating Browser-Use + Playwright integration with custom actions.
	"""
	print('🚀 Advanced Playwright + Browser-Use Integration with Custom Actions')

	chrome_process = None
	try:
		# Step 1: Start Chrome with CDP debugging
		chrome_process = await start_chrome_with_debug_port()
		cdp_url = 'http://localhost:9222'

		# Step 2: Connect Playwright to the same Chrome instance
		await connect_playwright_to_cdp(cdp_url)

		# Step 3: Create Browser-Use session connected to same Chrome
		browser_session = BrowserSession(cdp_url=cdp_url)

		# Step 4: Create AI agent with our custom Playwright-powered tools
		agent = Agent(
			task="""
			Please help me demonstrate the integration between Browser-Use and Playwright:
			
			1. First, navigate to https://httpbin.org/forms/post
			2. Use the 'playwright_fill_form' action to fill the form with these details:
			   - Customer name: "Alice Johnson"
			   - Phone: "555-9876"
			   - Email: "alice@demo.com"
			   - Size: "large"
			3. Take a screenshot using the 'playwright_screenshot' action and save it as "form_demo.png"
			4. Extract the title of the page using 'playwright_get_text' action with selector "title"
			5. Finally, submit the form and tell me what happened
			
			This demonstrates how Browser-Use AI can orchestrate tasks while using Playwright's precise capabilities for specific operations.
			""",
			llm=ChatOpenAI(model='gpt-4.1-mini'),
			tools=tools,  # Our custom tools with Playwright actions
			browser_session=browser_session,
		)

		print('🎯 Starting AI agent with custom Playwright actions...')

		# Step 5: Run the agent - it will use both Browser-Use actions and our custom Playwright actions
		result = await agent.run()

		# Keep browser open briefly to see results
		print(f'✅ Integration demo completed! Result: {result}')
		await asyncio.sleep(2)  # Brief pause to see results

	except Exception as e:
		print(f'❌ Error: {e}')
		raise

	finally:
		# Clean up resources
		if playwright_browser:
			await playwright_browser.close()

		if chrome_process:
			chrome_process.terminate()
			try:
				await asyncio.wait_for(chrome_process.wait(), 5)
			except TimeoutError:
				chrome_process.kill()

		print('✅ Cleanup complete')


if __name__ == '__main__':
	# Run the advanced integration demo
	asyncio.run(main())
