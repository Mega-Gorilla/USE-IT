import os
import sys
from dotenv import load_dotenv
import asyncio

# Add the root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import SecretStr

from langchain_google_genai import ChatGoogleGenerativeAI
from browser_use.agent.service import Agent
from discord_api import DiscordBot


load_dotenv()


# load credentials from environment variables
bot_token = os.getenv("DISCORD_BOT_TOKEN")
if not bot_token:
	raise ValueError("Discord bot token not found in .env file.")

api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
	raise ValueError('GEMINI_API_KEY is not set')

llm = ChatGoogleGenerativeAI(model='gemini-2.0-flash-exp', api_key=SecretStr(api_key))

bot = DiscordBot(llm=llm)
bot.run(bot_token)
