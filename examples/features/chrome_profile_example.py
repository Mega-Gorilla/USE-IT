"""
Example of using Chrome profiles with browser-use.

This example demonstrates how to use existing Chrome profiles with saved logins and extensions.
"""

import asyncio
from browser_use import Agent
from browser_use.browser import BrowserSession, BrowserConfig


async def main():
    # Example 1: Use a specific Chrome profile with saved logins
    browser_session = BrowserSession(
        user_data_dir="~/Library/Application Support/Google/Chrome",
        profile_directory="Profile 1"  # Use "Default" for the default profile
    )
    
    agent = Agent(
        task="Check my Gmail inbox",
        browser_session=browser_session
    )
    
    await agent.run()
    
    # Example 2: Connect to an existing Chrome instance via CDP
    # First, start Chrome with: 
    # /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
    
    browser_session_cdp = BrowserSession(
        cdp_url="http://localhost:9222"
    )
    
    agent_cdp = Agent(
        task="Navigate to GitHub and show my profile",
        browser_session=browser_session_cdp
    )
    
    await agent_cdp.run()


if __name__ == "__main__":
    asyncio.run(main()) 