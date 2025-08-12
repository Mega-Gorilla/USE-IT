import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from amazoncaptcha import AmazonCaptcha  # type: ignore

from browser_use import ActionResult
from browser_use.agent.service import Agent
from browser_use.browser import BrowserConfig, BrowserSession
from browser_use.controller.service import Controller
from browser_use.llm import ChatOpenAI

browser_profile = BrowserConfig(headless=False)

# Initialize controller first
controller = Controller()


@controller.action(
	'Solve Amazon text based captcha',
	domains=[
		'*.amazon.com',
		'*.amazon.co.uk',
		'*.amazon.ca',
		'*.amazon.de',
		'*.amazon.es',
		'*.amazon.fr',
		'*.amazon.it',
		'*.amazon.co.jp',
		'*.amazon.in',
		'*.amazon.cn',
		'*.amazon.com.sg',
		'*.amazon.com.mx',
		'*.amazon.ae',
		'*.amazon.com.br',
		'*.amazon.nl',
		'*.amazon.com.au',
		'*.amazon.com.tr',
		'*.amazon.sa',
		'*.amazon.se',
		'*.amazon.pl',
	],
)
async def solve_amazon_captcha(browser_session: BrowserSession):
	if not browser_session.agent_focus:
		raise ValueError('No active browser session')

	# Find the captcha image and extract its src using CDP
	result = await browser_session.agent_focus.cdp_client.send.Runtime.evaluate(
		params={
			'expression': """
				const img = document.querySelector('img[src*="amazon.com/captcha"]');
				img ? img.src : null;
			""",
			'returnByValue': True,
		},
		session_id=browser_session.agent_focus.session_id,
	)
	link = result.get('result', {}).get('value')

	if not link:
		raise ValueError('Could not find captcha image on the page')

	captcha = AmazonCaptcha.fromlink(link)
	solution = captcha.solve()
	if not solution or solution == 'Not solved':
		raise ValueError('Captcha could not be solved')

	# Fill the captcha solution using CDP
	await browser_session.agent_focus.cdp_client.send.Runtime.evaluate(
		params={
			'expression': f"""
				const input = document.querySelector('#captchacharacters');
				if (input) {{
					input.value = '{solution}';
					input.dispatchEvent(new Event('input', {{ bubbles: true }}));
					input.dispatchEvent(new Event('change', {{ bubbles: true }}));
				}}
			""",
		},
		session_id=browser_session.agent_focus.session_id,
	)

	# Click submit button using CDP
	await browser_session.agent_focus.cdp_client.send.Runtime.evaluate(
		params={
			'expression': """
				const button = document.querySelector('button[type="submit"]');
				if (button) button.click();
			""",
		},
		session_id=browser_session.agent_focus.session_id,
	)

	return ActionResult(extracted_content=solution)


async def main():
	task = 'Go to https://www.amazon.com/errors/validateCaptcha and solve the captcha using the solve_amazon_captcha tool'

	model = ChatOpenAI(model='gpt-4.1')
	browser_session = BrowserSession(browser_profile=browser_profile)
	await browser_session.start()
	agent = Agent(task=task, llm=model, controller=controller, browser_session=browser_session)

	await agent.run()
	await browser_session.kill()

	input('Press Enter to close...')


if __name__ == '__main__':
	asyncio.run(main())
