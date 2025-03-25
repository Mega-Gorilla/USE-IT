"""
Playwright browser on steroids.
"""

import asyncio
import gc
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.utils import time_execution_async
from playwright._impl._api_structures import ProxySettings
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import (
	Playwright,
	async_playwright,
)

logger = logging.getLogger(__name__)


IN_DOCKER = os.environ.get("IN_DOCKER", "false").lower() == "true"


@dataclass
class BrowserConfig:
	r"""
	Configuration for the Browser.

	Default values:
					headless: True
									Whether to run browser in headless mode

					disable_security: True
									Disable browser security features

					extra_browser_args: []
									Extra arguments to pass to the browser

					wss_url: None
									Connect to a browser instance via WebSocket

					cdp_url: None
									Connect to a browser instance via CDP

					browser_instance_path: None
									Path to a Browser instance to use to connect to your normal browser
									e.g. '/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome'
	"""

	headless: bool = False
	disable_security: bool = True
	extra_browser_args: list[str] = field(default_factory=list)
	browser_instance_path: str | None = None
	wss_url: str | None = None
	cdp_url: str | None = None

	proxy: ProxySettings | None = field(default=None)
	new_context_config: BrowserContextConfig = field(default_factory=BrowserContextConfig)

	_force_keep_browser_alive: bool = False
	browser_class: Literal["chromium", "firefox", "webkit"] = "chromium"


# @singleton: TODO - think about id singleton makes sense here
# @dev By default this is a singleton, but you can create multiple instances if you need to.
class Browser:
	"""
	Playwright browser on steroids.

	This is persistant browser factory that can spawn multiple browser contexts.
	It is recommended to use only one instance of Browser per your application (RAM usage will grow otherwise).
	"""

	def __init__(
		self,
		config: BrowserConfig = BrowserConfig(),
	):
		logger.debug("Initializing new browser")
		self.config = config
		self.playwright: Playwright | None = None
		self.playwright_browser: PlaywrightBrowser | None = None

		self.disable_security_args = []
		if self.config.disable_security:
			self.disable_security_args = ["--disable-web-security", "--disable-site-isolation-trials"]
			if self.config.browser_class == "chromium":
				self.disable_security_args += [
					"--disable-features=IsolateOrigins,site-per-process",
				]

	async def new_context(self, config: BrowserContextConfig = BrowserContextConfig()) -> BrowserContext:
		"""Create a browser context"""
		return BrowserContext(config=config, browser=self)

	async def get_playwright_browser(self) -> PlaywrightBrowser:
		"""Get a browser context"""
		if self.playwright_browser is None:
			return await self._init()

		return self.playwright_browser

	@time_execution_async("--init (browser)")
	async def _init(self):
		"""Initialize the browser session"""
		playwright = await async_playwright().start()
		browser = await self._setup_browser(playwright)

		self.playwright = playwright
		self.playwright_browser = browser

		return self.playwright_browser

	async def _setup_cdp(self, playwright: Playwright) -> PlaywrightBrowser:
		"""Sets up and returns a Playwright Browser instance with anti-detection measures. Firefox has no longer CDP support."""
		if "firefox" in (self.config.browser_instance_path or "").lower():
			raise ValueError(
				"CDP has been deprecated for firefox, check: https://fxdx.dev/deprecating-cdp-support-in-firefox-embracing-the-future-with-webdriver-bidi/"
			)
		if not self.config.cdp_url:
			raise ValueError("CDP URL is required")
		logger.info(f"Connecting to remote browser via CDP {self.config.cdp_url}")
		browser_class = getattr(playwright, self.config.browser_class)
		browser = await browser_class.connect_over_cdp(self.config.cdp_url)

		return browser

	async def _setup_wss(self, playwright: Playwright) -> PlaywrightBrowser:
		"""Sets up and returns a Playwright Browser instance with anti-detection measures."""
		if not self.config.wss_url:
			raise ValueError("WSS URL is required")
		logger.info(f"Connecting to remote browser via WSS {self.config.wss_url}")
		browser_class = getattr(playwright, self.config.browser_class)
		browser = await browser_class.connect(self.config.wss_url)
		return browser

	async def _setup_browser_with_instance(self, playwright: Playwright) -> PlaywrightBrowser:
		"""Sets up and returns a Playwright Browser instance with anti-detection measures."""
		if not self.config.browser_instance_path:
			raise ValueError("Chrome instance path is required")
		import subprocess

		import requests

		try:
			# Check if browser is already running
			response = requests.get("http://localhost:9222/json/version", timeout=2)
			if response.status_code == 200:
				logger.info("Reusing existing Chrome instance")
				browser_class = getattr(playwright, self.config.browser_class)
				browser = await browser_class.connect_over_cdp(
					endpoint_url="http://localhost:9222",
					timeout=20000,  # 20 second timeout for connection
				)
				return browser
		except requests.ConnectionError:
			logger.debug("No existing Chrome instance found, starting a new one")

		# Start a new Chrome instance
		chrome_launch_cmd = list(
			{
				self.config.browser_instance_path,
				*CHROME_ARGS,
				*(CHROME_DOCKER_ARGS if IN_DOCKER else []),
				*(CHROME_HEADLESS_ARGS if self.config.headless else []),
				*self.config.extra_browser_args,
			}
		)
		subprocess.Popen(
			chrome_launch_cmd,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)

		# Attempt to connect again after starting a new instance
		for _ in range(10):
			try:
				response = requests.get("http://localhost:9222/json/version", timeout=2)
				if response.status_code == 200:
					break
			except requests.ConnectionError:
				pass
			await asyncio.sleep(1)

		# Attempt to connect again after starting a new instance
		try:
			browser_class = getattr(playwright, self.config.browser_class)
			browser = await browser_class.connect_over_cdp(
				endpoint_url="http://localhost:9222",
				timeout=20000,  # 20 second timeout for connection
			)
			return browser
		except Exception as e:
			logger.error(f"Failed to start a new Chrome instance.: {str(e)}")
			raise RuntimeError(
				" To start chrome in Debug mode, you need to close all existing Chrome instances and try again otherwise we can not connect to the instance."
			)

	async def _setup_standard_browser(self, playwright: Playwright) -> PlaywrightBrowser:
		"""Sets up and returns a Playwright Browser instance with anti-detection measures."""
		browser_class = getattr(playwright, self.config.browser_class)

		args = {
			"chromium": [
				*(CHROME_DOCKER_ARGS if IN_DOCKER else []),
				*(CHROME_HEADLESS_ARGS if self.config.headless else []),
				*CHROME_ARGS,
				*self.disable_security_args,
				*self.config.extra_browser_args,
			],
			"firefox": [
				"-no-remote",
				*self.disable_security_args,
				*self.config.extra_browser_args,
			],
			"webkit": [
				"--no-startup-window",
				*self.disable_security_args,
				*self.config.extra_browser_args,
			],
		}
		browser = await browser_class.launch(
			headless=self.config.headless,
			args=args[self.config.browser_class],
			proxy=self.config.proxy,
		)
		# convert to Browser
		return browser

	async def _setup_browser(self, playwright: Playwright) -> PlaywrightBrowser:
	   """Sets up and returns a Playwright Browser instance with anti-detection measures."""
		try:
			if self.config.cdp_url:
				return await self._setup_cdp(playwright)
			if self.config.wss_url:
				 return await self._setup_wss(playwright)
			elif self.config.browser_instance_path:
				return await self._setup_browser_with_instance(playwright)
			else:
				return await self._setup_standard_browser(playwright)
		except Exception as e:
			logger.error(f"Failed to initialize Playwright browser: {str(e)}")
			raise

	async def close(self):
		"""Close the browser instance"""
		try:
			if not self.config._force_keep_browser_alive:
				if self.playwright_browser:
					await self.playwright_browser.close()
					del self.playwright_browser
				if self.playwright:
					await self.playwright.stop()
					del self.playwright

		except Exception as e:
			logger.debug(f"Failed to close browser properly: {e}")
		finally:
			self.playwright_browser = None
			self.playwright = None

			gc.collect()

	def __del__(self):
		"""Async cleanup when object is destroyed"""
		try:
			if self.playwright_browser or self.playwright:
				loop = asyncio.get_running_loop()
				if loop.is_running():
					loop.create_task(self.close())
				else:
					asyncio.run(self.close())
		except Exception as e:
			logger.debug(f"Failed to cleanup browser in destructor: {e}")


DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
CHROME_EXTENSIONS = {}
CHROME_EXTENSIONS_PATH = "chrome_extensions"
CHROME_PROFILE_PATH = "chrome_profile"
CHROME_PROFILE_USER = "Default"
CHROME_DEBUG_PORT = 9222
CHROME_DISABLED_COMPONENTS = [
	"Translate",
	"AcceptCHFrame",
	"OptimizationHints",
	"ProcessPerSiteUpToMainFrameThreshold",
	"InterestFeedContentSuggestions",
	"CalculateNativeWinOcclusion",
	"BackForwardCache",
	"HeavyAdPrivacyMitigations",
	"LazyFrameLoading",
	"ImprovedCookieControls",
	"PrivacySandboxSettings4",
	"AutofillServerCommunication",
	"CertificateTransparencyComponentUpdater",
	"DestroyProfileOnBrowserClose",
	"CrashReporting",
	"OverscrollHistoryNavigation",
	"InfiniteSessionRestore",
	#'LockProfileCookieDatabase',  # disabling allows multiple chrome instances to concurrently modify profile, but might make chrome much slower https://github.com/yt-dlp/yt-dlp/issues/7271  https://issues.chromium.org/issues/40901624
]  # it's always best to give each chrome instance its own exclusive copy of the user profile


CHROME_HEADLESS_ARGS = [
	"--headless=new",
	"--test-type",
	"--test-type=gpu",  # https://github.com/puppeteer/puppeteer/issues/10516
	# '--enable-automation',                            # <- DONT USE THIS, it makes you easily detectable / blocked by cloudflare
]

CHROME_DOCKER_ARGS = [
	# Docker-specific options
	# https://github.com/GoogleChrome/lighthouse-ci/tree/main/docs/recipes/docker-client#--no-sandbox-issues-explained
	"--no-sandbox",  # rely on docker sandboxing in docker, otherwise we need cap_add: SYS_ADM to use host sandboxing
	"--disable-gpu-sandbox",
	"--disable-setuid-sandbox",
	"--disable-dev-shm-usage",  # docker 75mb default shm size is not big enough, disabling just uses /tmp instead
	"--no-xshm",
]

CHROME_ARGS = [
	# Profile data dir setup
	# chrome://profile-internals
	f"--user-data-dir={CHROME_PROFILE_PATH}",
	f"--profile-directory={CHROME_PROFILE_USER}",
	"--password-store=basic",  # use mock keychain instead of OS-provided keychain (we manage auth.json instead)
	"--use-mock-keychain",
	"--disable-cookie-encryption",  # we need to be able to write unencrypted cookies to save/load auth.json
	"--disable-sync",  # don't try to use Google account sync features while automation is active
	# Extensions
	# chrome://inspect/#extensions
	# f'--load-extension={CHROME_EXTENSIONS.map(({unpacked_path}) => unpacked_path).join(',')}',  # not needed when using existing profile that already has extensions installed
	f"--allowlisted-extension-id={','.join(CHROME_EXTENSIONS.keys())}",
	"--allow-legacy-extension-manifests",
	"--deterministic-mode",
	"--js-flags=--random-seed=1157259159",  # make all JS random numbers deterministic by providing a seed
	"--allow-pre-commit-input",  # allow JS mutations before page rendering is complete
	"--disable-blink-features=AutomationControlled",  # hide the signatures that announce browser is being remote-controlled
	# f'--proxy-server=https://43.159.28.126:2334:u7ce652b7568805c4-zone-custom-region-us-session-szGWq3FRU-sessTime-60:u7ce652b7568805c4',      # send all network traffic through a proxy https://2captcha.com/proxy
	# f'--proxy-bypass-list=127.0.0.1',
	# Browser window and viewport setup
	# chrome://version
	# f'--user-agent="{DEFAULT_USER_AGENT}"',
	# f'--window-size={DEFAULT_VIEWPORT.width},{DEFAULT_VIEWPORT.height}',
	"--window-position=0,0",
	"--start-maximized",
	"--hide-scrollbars",  # hide scrollbars because otherwise they show up in screenshots
	"--install-autogenerated-theme=0,0,0",  # black border makes it easier to see which chrome window is browser-use's
	#'--virtual-time-budget=60000',  # fast-forward all animations & timers by 60s, dont use this it's unfortunately buggy and breaks screenshot and PDF capture sometimes
	#'--autoplay-policy=no-user-gesture-required',  # auto-start videos so they trigger network requests + show up in outputs
	#'--disable-gesture-requirement-for-media-playback',
	#'--lang=en-US,en;q=0.9',
	# DANGER: JS isolation security features (to allow easier tampering with pages during archiving)
	# chrome://net-internals
	# '--disable-web-security',                              # <- WARNING, breaks some sites that expect/enforce strict CORS headers (try webflow.com)
	# '--disable-features=IsolateOrigins,site-per-process', // useful for injecting JS, but some very strict sites can panic / show error pages when isolation is disabled (e.g. webflow.com)
	# '--allow-running-insecure-content',                   # Breaks CORS/CSRF/HSTS etc., useful sometimes but very easy to detect
	# '--allow-file-access-from-files',                     # <- WARNING, dangerous, allows JS to read filesystem using file:// URLs
	# // DANGER: Disable HTTPS verification
	# '--ignore-certificate-errors',
	# '--ignore-ssl-errors',
	# '--ignore-certificate-errors-spki-list',
	# '--allow-insecure-localhost',
	# IO: stdin/stdout, debug port config
	# chrome://inspect
	"--log-level=2",  # 1=DEBUG 2=WARNING 3=ERROR
	"--enable-logging=stderr",
	"--remote-debugging-address=0.0.0.0",
	f"--remote-debugging-port={CHROME_DEBUG_PORT}",
	# GPU, canvas, text, and pdf rendering config
	# chrome://gpu
	"--enable-webgl",  # enable web-gl graphics support
	"--font-render-hinting=none",  # make rendering more deterministic by ignoring OS font hints, may also need css override, try:    * {text-rendering: geometricprecision !important; -webkit-font-smoothing: antialiased;}
	"--force-color-profile=srgb",  # make rendering more deterministic by using consitent color profile, if browser looks weird, try: generic-rgb
	"--disable-partial-raster",  # make rendering more deterministic (TODO: verify if still needed)
	"--disable-skia-runtime-opts",  # make rendering more deterministic by avoiding Skia hot path runtime optimizations
	"--disable-2d-canvas-clip-aa",  # make rendering more deterministic by disabling antialiasing on 2d canvas clips
	# '--disable-gpu',                                  # falls back to more consistent software renderer across all OS's, especially helps linux text rendering look less weird
	# // '--use-gl=swiftshader',                        <- DO NOT USE, breaks M1 ARM64. it makes rendering more deterministic by using simpler CPU renderer instead of OS GPU renderer  bug: https://groups.google.com/a/chromium.org/g/chromium-dev/c/8eR2GctzGuw
	# // '--disable-software-rasterizer',               <- DO NOT USE, harmless, used in tandem with --disable-gpu
	# // '--run-all-compositor-stages-before-draw',     <- DO NOT USE, makes headful chrome hang on startup (tested v121 Google Chrome.app on macOS)
	# // '--disable-gl-drawing-for-tests',              <- DO NOT USE, disables gl output (makes tests run faster if you dont care about canvas)
	# // '--blink-settings=imagesEnabled=false',        <- DO NOT USE, disables images entirely (only sometimes useful to speed up loading)
	# Process management & performance tuning
	# chrome://process-internals
	"--disable-lazy-loading",  # make rendering more deterministic by loading all content up-front instead of on-focus
	"--disable-renderer-backgrounding",  # dont throttle tab rendering based on focus/visibility
	"--disable-background-networking",  # dont throttle tab networking based on focus/visibility
	"--disable-background-timer-throttling",  # dont throttle tab timers based on focus/visibility
	"--disable-backgrounding-occluded-windows",  # dont throttle tab window based on focus/visibility
	"--disable-ipc-flooding-protection",  # dont throttle ipc traffic or accessing big request/response/buffer/etc. objects will fail
	"--disable-extensions-http-throttling",  # dont throttle http traffic based on runtime heuristics
	"--disable-field-trial-config",  # disable shared field trial state between browser processes
	"--disable-back-forward-cache",  # disable browsing navigation cache
	# '--in-process-gpu',                            <- DONT USE THIS, makes headful startup time ~5-10s slower (tested v121 Google Chrome.app on macOS)
	# '--disable-component-extensions-with-background-pages',  # TODO: check this, disables chrome components that only run in background with no visible UI (could lower startup time)
	# uncomment to disable hardware camera/mic/speaker access + present fake devices to websites
	# (faster to disable, but disabling breaks recording browser audio in puppeteer-stream screenrecordings)
	# '--use-fake-device-for-media-stream',
	# '--use-fake-ui-for-media-stream',
	# '--disable-features=GlobalMediaControls,MediaRouter,DialMediaRouteProvider',
	# Output format options (PDF, screenshot, etc.)
	"--export-tagged-pdf",  # include table on contents and tags in printed PDFs
	"--generate-pdf-document-outline",
	# Suppress first-run features, popups, hints, updates, etc.
	# chrome://system
	"--no-pings",
	"--no-first-run",
	"--no-default-browser-check",
	"--disable-default-apps",
	"--ash-no-nudges",
	"--disable-infobars",
	"--disable-search-engine-choice-screen",
	"--disable-session-crashed-bubble",
	'--simulate-outdated-no-au="Tue, 31 Dec 2099 23:59:59 GMT"',  # disable browser self-update while automation is active
	"--hide-crash-restore-bubble",
	"--suppress-message-center-popups",
	"--disable-client-side-phishing-detection",
	"--disable-domain-reliability",
	"--disable-component-update",
	"--disable-datasaver-prompt",
	"--disable-hang-monitor",
	"--disable-session-crashed-bubble",
	"--disable-speech-synthesis-api",
	"--disable-speech-api",
	"--disable-print-preview",
	"--safebrowsing-disable-auto-update",
	"--deny-permission-prompts",
	"--disable-external-intent-requests",
	"--disable-notifications",
	"--disable-desktop-notifications",
	"--noerrdialogs",
	"--disable-popup-blocking",
	"--disable-prompt-on-repost",
	"--silent-debugger-extension-api",
	"--block-new-web-contents",
	"--metrics-recording-only",
	"--disable-breakpad",
	# other feature flags
	# chrome://flags        chrome://components
	f"--disable-features={','.join(CHROME_DISABLED_COMPONENTS)}",
	"--enable-features=NetworkService",
]
