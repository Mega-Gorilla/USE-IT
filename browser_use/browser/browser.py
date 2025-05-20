from browser_use.browser.session import BrowserSession
from browser_use.browser.types import BrowserProfile

BrowserConfig = BrowserProfile
BrowserContextConfig = BrowserProfile
Browser = BrowserSession

__all__ = ['BrowserConfig', 'BrowserContextConfig', 'Browser']
