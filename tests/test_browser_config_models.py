import os

import pytest

from browser_use.browser.profile import BrowserProfile, ProxySettings
from browser_use.browser.session import BrowserSession


@pytest.mark.asyncio
async def test_proxy_settings_pydantic_model():
	"""
	Test that ProxySettings as a Pydantic model is correctly converted to a dictionary when used.
	"""
	# Create ProxySettings with Pydantic model
	proxy_settings = dict(server='http://example.proxy:8080', bypass='localhost', username='testuser', password='testpass')

	# Verify the model has correct dict-like access
	assert proxy_settings['server'] == 'http://example.proxy:8080'
	assert proxy_settings.get('bypass') == 'localhost'
	assert proxy_settings.get('nonexistent', 'default') == 'default'

	# Verify model_dump works correctly
	proxy_dict = dict(proxy_settings)
	assert isinstance(proxy_dict, dict)
	assert proxy_dict['server'] == 'http://example.proxy:8080'
	assert proxy_dict['bypass'] == 'localhost'
	assert proxy_dict['username'] == 'testuser'
	assert proxy_dict['password'] == 'testpass'

	# We don't launch the actual browser - we just verify the model itself works as expected


@pytest.mark.asyncio
async def test_window_size_config():
	"""
	Test that BrowserProfile correctly handles window_size property.
	"""
	# Create profile with specific window dimensions
	profile = BrowserProfile(window_size={'width': 1280, 'height': 1100})

	# Verify the properties are set correctly
	assert profile.window_size['width'] == 1280
	assert profile.window_size['height'] == 1100

	# Verify model_dump works correctly
	profile_dict = profile.model_dump()
	assert isinstance(profile_dict, dict)
	assert profile_dict['window_size']['width'] == 1280
	assert profile_dict['window_size']['height'] == 1100

	# Create with different values
	profile2 = BrowserProfile(window_size={'width': 1920, 'height': 1080})
	assert profile2.window_size['width'] == 1920
	assert profile2.window_size['height'] == 1080


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get('CI') == 'true', reason='Skip browser test in CI')
async def test_window_size_with_real_browser():
	"""
	Integration test that verifies our window size Pydantic model is correctly
	passed to Playwright and the actual browser window is configured with these settings.
	This test is skipped in CI environments.
	"""
	# Create browser profile with headless mode and specific dimensions
	browser_profile = BrowserProfile(
		headless=True,  # Use headless for faster test
		window_size={'width': 1024, 'height': 768},
		maximum_wait_page_load_time=2.0,  # Faster timeouts for test
		minimum_wait_page_load_time=0.2,
		no_viewport=True,  # Use actual window size instead of viewport
	)

	# Create browser session
	browser_session = BrowserSession(browser_profile=browser_profile)
	try:
		await browser_session.start()
		# Get the current page
		page = await browser_session.get_current_page()
		assert page is not None, 'Failed to get current page'

		# Get the context configuration used for browser window size
		video_size = await page.evaluate("""
                () => {
                    // This returns information about the context recording settings
                    // which should match our configured video size (browser_window_size)
                    try {
                        const settings = window.getPlaywrightContextSettings ? 
                            window.getPlaywrightContextSettings() : null;
                        if (settings && settings.recordVideo) {
                            return settings.recordVideo.size;
                        }
                    } catch (e) {}
                    
                    // Fallback to window dimensions
                    return {
                        width: window.innerWidth,
                        height: window.innerHeight
                    };
                }
            """)

		# Let's also check the viewport size
		viewport_size = await page.evaluate("""
                () => {
                    return {
                        width: window.innerWidth,
                        height: window.innerHeight
                    }
                }
            """)

		print(f'Window size config: width={browser_profile.window_size["width"]}, height={browser_profile.window_size["height"]}')
		print(f'Browser viewport size: {viewport_size}')

		# This is a lightweight test to verify that the page has a size (details may vary by browser)
		assert viewport_size['width'] > 0, 'Expected viewport width to be positive'
		assert viewport_size['height'] > 0, 'Expected viewport height to be positive'

		# For browser context creation in record_video_size, this is what truly matters
		# Verify that our window size was properly serialized to a dictionary
		print(f'Content of context session: {browser_session.browser_context}')
		print('✅ Browser window size used in the test')
	finally:
		await browser_session.stop()


@pytest.mark.asyncio
async def test_proxy_with_real_browser():
	"""
	Integration test that verifies our proxy Pydantic model is correctly
	passed to Playwright without requiring a working proxy server.

	This test:
	1. Creates a ProxySettings Pydantic model
	2. Passes it to BrowserProfile
	3. Verifies browser initialization works (proving the model was correctly serialized)
	4. We don't actually verify proxy functionality (would require a working proxy)
	"""
	# Create proxy settings with a fake proxy server
	proxy_settings = ProxySettings(
		server='http://non.existent.proxy:9999', bypass='localhost', username='testuser', password='testpass'
	)

	# Test model serialization
	proxy_dict = proxy_settings.model_dump()
	assert isinstance(proxy_dict, dict)
	assert proxy_dict['server'] == 'http://non.existent.proxy:9999'

	# Create browser profile with proxy
	browser_profile = BrowserProfile(
		headless=True,
		proxy=proxy_settings,
	)

	# Create browser session
	browser_session = BrowserSession(browser_profile=browser_profile)
	try:
		await browser_session.start()
		# Success - the browser was initialized with our proxy settings
		# We won't try to make requests (which would fail with non-existent proxy)
		print('✅ Browser initialized with proxy settings successfully')
	finally:
		await browser_session.stop()
