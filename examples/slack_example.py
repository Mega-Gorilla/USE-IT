"""
This example requires you to have a Slack bot token and signing secret.

Steps to create and configure a Slack bot:

1. Create a Slack App:
    *   Go to the Slack API: https://api.slack.com/apps
    *   Click on "Create New App".
    *   Choose "From scratch" and give your app a name and select the workspace.
2. Configure the Bot:
    *   Navigate to the "OAuth & Permissions" tab on the left side of the screen.
    *   Under "Scopes", add the necessary bot token scopes (e.g., "chat:write", "channels:history", "im:history").
    *   Install the app to your workspace and copy the "Bot User OAuth Token".
3. Enable Event Subscriptions:
    *   Navigate to the "Event Subscriptions" tab.
    *   Enable events and add the necessary bot events (e.g., "message.channels", "message.im").
    *   Add your request URL (you can use ngrok to expose your local server if needed).
4. Save the "Signing Secret" from the "Basic Information" tab.
5. Add the bot to your Slack workspace.
6. Run the code below to start the bot with your bot token and signing secret.
7. Write e.g. "$bu whats the weather in Tokyo?" to start a browser-use task and get a response inside the Slack channel.
"""

import os
from dotenv import load_dotenv
from integrations.slack_api import SlackBot, app
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr
from browser_use import BrowserConfig
from fastapi import Depends

load_dotenv()

# load credentials from environment variables
bot_token = os.getenv('SLACK_BOT_TOKEN')
if not bot_token:
    raise ValueError('Slack bot token not found in .env file.')

signing_secret = os.getenv('SLACK_SIGNING_SECRET')
if not signing_secret:
    raise ValueError('Slack signing secret not found in .env file.')

api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
    raise ValueError('GEMINI_API_KEY is not set')

llm = ChatGoogleGenerativeAI(model='gemini-2.0-flash-exp', api_key=SecretStr(api_key))

slack_bot = SlackBot(
    llm=llm,  # required; instance of BaseChatModel
    bot_token=bot_token,  # required; Slack bot token
    signing_secret=signing_secret,  # required; Slack signing secret
    ack=True,  # optional; whether to acknowledge task receipt with a message, defaults to False
    browser_config=BrowserConfig(
        headless=True
    ),  # optional; useful for changing headless mode or other browser configs, defaults to headless mode
)

app.dependency_overrides[SlackBot] = lambda: slack_bot

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("integrations.slack_api:app", host="0.0.0.0", port=3000, reload=True)