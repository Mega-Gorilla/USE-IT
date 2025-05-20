from browser_use.browser.session import BrowserSession
from browser_use.browser.types import BrowserProfile

Browser = BrowserSession
BrowserConfig = BrowserProfile
BrowserContext = BrowserSession
BrowserContextConfig = BrowserProfile

__all__ = ['Browser', 'BrowserConfig', 'BrowserContext', 'BrowserContextConfig']
