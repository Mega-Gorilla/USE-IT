DEPRECATION_MESSAGE = """
⚠️ {} is deprecated. Use the ✨ new, simplified BrowserSession(**all_config_here) ✨ instead!\n
	from browser_use.browser import BrowserSession, BrowserProfile
 
	browser_profile = BrowserProfile(
		# ⬇️ the old BrowserConfig, BrowserContextConfig kwargs all go in one place now:
		headless=False,
		disable_security=False,
		keep_alive=True,
		extra_browser_args=['--start-maximized'],
		allowed_domains=['example.com', '*.google.com'],
		browser_binary_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		...
	)

	# then start a session using the profile to connect/launch to the live browser:
	browser_session = BrowserSession(
		profile=browser_profile,
		# pass a CDP URL to connect to an existing browser instance:
		cdp_url='http://localhost:9222',
		# or provide a custom binary to launch:
		browser_binary_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		# or pass live playwright or patchright objects to use them directly:
		browser=playwright_browser,
		browser_context=playwright_browser_context,
	)
	agent = Agent(browser_session=browser_session, ...)
	...
"""


class DeprecatedClass:
	def __init__(self, *_args, **_kwargs):
		print(DEPRECATION_MESSAGE.format(type(self).__name__))
		raise NotImplementedError(
			f'{type(self).__name__} is deprecated. Use browser_use.browser.BrowserProfile and BrowserSession instead.'
		)


class BrowserConfig(DeprecatedClass):
	pass


class BrowserContextConfig(DeprecatedClass):
	pass


class Browser(DeprecatedClass):
	pass


class BrowserContext(DeprecatedClass):
	pass
