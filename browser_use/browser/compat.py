DEPRECATION_MESSAGE = """
⚠️ {} is deprecated. Use the ✨ new, simplified BrowserProfile and BrowserSession ✨ instead!\n
	from browser_use.browser import BrowserProfile, BrowserSession
 
	browser_profile = BrowserProfile(
		# ⬇️ the old BrowserConfig, BrowserContextConfig, and playwright launch/context kwargs all go in one place now:
		headless=False,
		disable_security=False,
		keep_alive=True,
		args=['--start-maximized'],
		ignore_default_args=['--enable-automation'],
		allowed_domains=['example.com', '*.google.com'],
		executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		user_data_dir='/path/to/user_data_dir',
		downloads_path='/path/to/downloads',
		...
		# ⬆️ see the full list of options here: https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context
	)

	# then start a session using the profile to connect/launch to the live browser:
	browser_session = BrowserSession(
		browser_profile=browser_profile,
		# pass a CDP URL to connect to an existing browser instance:
		cdp_url='http://localhost:9222',
		# or provide a custom binary to launch:
		executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		# or pass live playwright or patchright objects to use an existing browser connection directly:
		browser=playwright_browser_obj,
		browser_context=playwright_browser_context_obj,
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
