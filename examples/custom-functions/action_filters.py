"""
This example demonstrates how to use action filters to expose different actions
based on the current page URL or page content.

There are two ways to filter actions:
1. Using `domains` parameter with glob patterns to match specific domains
2. Using `page_filter` function for more complex filtering based on page content

Actions with no filters will be included in the system prompt. Actions with filters
will only be dynamically added when the agent is on a matching page.
"""

import asyncio
import os
from typing import Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from browser_use.agent.service import Agent
from browser_use.browser.browser import Browser
from browser_use.controller.service import Controller, Registry

# Initialize controller and registry
controller = Controller()
registry = controller.registry


# Define a standard action with no filters (always available)
@registry.action(
    description="Navigate to a URL"
)
async def navigate(url: str):
    """Navigate to a specific URL"""
    return f"Would navigate to {url}"


# Action only available on Google
@registry.action(
    description="Search on Google",
    domains=["google.com", "*.google.com"]  # Matches google.com and any subdomain
)
async def google_search(query: str):
    """Perform a Google search"""
    return f"Would search for '{query}' on Google"


# Action only available on GitHub
@registry.action(
    description="Search GitHub repositories",
    domains=["github.com"]
)
async def github_search(repo_query: str):
    """Search for repositories on GitHub"""
    return f"Would search for GitHub repos matching '{repo_query}'"


# Action that uses a complex page filter function
# Note: For real use, you would need an async page filter or access HTML content via DOM
@registry.action(
    description="Login to account (only available on login pages)",
    page_filter=lambda page: "login" in page.url.lower() or 
                             page.url.endswith("signin") or
                             page.url.endswith("sign-in")
)
async def login(username: str, password: str):
    """Login to the current site"""
    return f"Would login with username '{username}'"


# Action for admin pages using both domain and page filter
@registry.action(
    description="Admin-only action",
    domains=["example.com"],
    page_filter=lambda page: "admin" in page.url.lower()
)
class AdminAction(BaseModel):
    action_type: str = Field(description="Type of admin action to perform")
    
    async def execute(self):
        return f"Would perform admin action: {self.action_type}"


async def main():
    """Main function to run the example"""
    browser = Browser()
    llm = ChatOpenAI(model_name="gpt-4o")
    
    # Create the agent
    agent = Agent(
        task="Demonstrate how action filters work by visiting different websites",
        llm=llm,
        browser=browser,
        controller=controller,
    )
    
    # Run the agent
    await agent.run(max_steps=10)
    
    # Cleanup
    await browser.close()


if __name__ == "__main__":
    asyncio.run(main())