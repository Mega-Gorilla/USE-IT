import os
import sys
from pathlib import Path

# Import necessary components from browser_use
from browser_use.agent.views import ActionResult

# Setup system path for project import
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import pyperclip
from langchain_openai import ChatOpenAI

from browser_use import Agent, Controller
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext

# Initialize the browser with configuration
browser = Browser(
    config=BrowserConfig(
        headless=False,  # Run the browser with a UI (set to True for headless mode)
    )
)

# Initialize the controller that handles actions
controller = Controller()


@controller.registry.action('Like Instagram Post')
async def like_instagram_post(post_url: str, browser: BrowserContext):
    """
    This action automates the process of liking an Instagram post.
    The function tries to locate the heart-shaped "like" button on the post and click it.
    If the button is not directly identifiable, it attempts to click within a fallback radius
    where the heart icon is expected to be located.
    """

    # Get the current page from the browser context
    page = await browser.get_current_page()
    
    # Navigate to the provided Instagram post URL
    await page.goto(post_url)

    # Try to locate the heart-shaped SVG element (which represents the "like" button)
    like_button = await page.query_selector('svg[aria-label="Like"]')

    if like_button:
        # If the heart-shaped "like" button is found, click it
        await like_button.click()
        return ActionResult(extracted_content="Successfully liked the post.")  # Return success result
    else:
        # If the "like" button is not found, proceed with the fallback radius mechanism
        heart_icon_position = await get_heart_icon_position(page)

        if heart_icon_position:
            # If a valid position for the heart icon is found, click within the radius
            await page.mouse.click(heart_icon_position[0], heart_icon_position[1])
            return ActionResult(extracted_content="Successfully liked the post by clicking within radius.")
        else:
            # If the heart icon and position are not found, return a failure message
            return ActionResult(extracted_content="Failed to locate or interact with the like button.")


async def get_heart_icon_position(page):
    """
    This function calculates the position of the heart-shaped "like" button on the page.
    It attempts to find the element's bounding box (position and size).
    If the bounding box is found, it returns the center of the heart icon's position.
    
    This function is used as a fallback if the heart icon cannot be directly located
    by the initial selector.
    """
    try:
        # Query the page to find the heart-shaped SVG element
        heart_element = await page.query_selector('svg[aria-label="Like"]')

        # Get the bounding box of the heart icon (its position and size on the page)
        box = await heart_element.bounding_box()

        if box:
            # If a bounding box is found, calculate the center of the heart icon
            # This ensures that we click the middle of the icon, regardless of its size or position
            return (box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    except Exception as e:
        # In case of any error finding the position (e.g., element not found or bounding box failure)
        print(f"Error finding heart icon position: {e}")
    
    # Return None if the position couldn't be found or an error occurred
    return None


async def main():
    """
    The main function that initializes the agent and runs the Instagram "like" task.
    It specifies the task (like the Instagram post), loads the model (GPT-4), and runs the agent.
    After the task is complete, it closes the browser.
    """
    # Define the task description for the agent: navigate to Instagram, like the post, and handle fallback if needed
    task = f'Go to an Instagram post URL and "like" the photo by clicking the heart button. Ensure that if the heart button is not visible, you click within its expected radius.'
    
    # Initialize the GPT-4 model for the agent to use
    model = ChatOpenAI(model='gpt-4o')
    
    # Create an agent instance with the task, model, controller, and browser context
    agent = Agent(
        task=task,
        llm=model,
        controller=controller,
        browser=browser,
    )

    # Example Instagram post URL (replace with an actual post URL)
    post_url = "https://www.instagram.com/p/CjHgkZlLrLz/"  # Replace with the actual post URL

    # Run the agent to perform the task (automating the Instagram like)
    await agent.run()

    # Close the browser once the task is done
    await browser.close()

    # Wait for the user to press Enter before closing the script
    input('Press Enter to close...')


if __name__ == '__main__':
    # Run the main function in an asynchronous event loop
    asyncio.run(main())
