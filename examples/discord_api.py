import os

from langchain_core.language_models.chat_models import BaseChatModel
from dotenv import load_dotenv

import discord
from discord.ext import commands
from discord import app_commands

from browser_use.agent.service import Agent, Browser
from browser_use import BrowserConfig



load_dotenv()  

class DiscordBot(commands.Bot):
    def __init__(self, llm: BaseChatModel):
        self.llm = llm

        # Define intents.
        intents = discord.Intents.default()
        intents.message_content = True  # Enable message content intent
        intents.members = True # Enable members intent for user info

        # Initialize the bot with a command prefix and intents.
        super().__init__(command_prefix="!", intents=intents) # You may not need prefix, just here for flexibility

        # self.tree = app_commands.CommandTree(self) # Initialize command tree for slash commands.

    async def on_ready(self):
        """Called when the bot is ready."""
        print(f'We have logged in as {self.user}')
        cmds = await self.tree.sync() # Sync the command tree with discord
        print(f"Synced commands: {cmds}")

    async def on_message(self, message):
       """Called when a message is received."""
       if message.author == self.user: # Ignore the bot's messages
            return
       if message.content.strip().startswith("$bu "):
            
            # await message.reply(
            #         "Starting browser use task...",
            #         mention_author=True  # Don't ping the user
            #     )

            # run browser-use
            agent_message = await self.run_agent(message.content.replace("$bu ", "").strip())

            await message.channel.send(
                    content=f"{agent_message}",
                    reference=message,
                    mention_author=True
                )
            
    #    await self.process_commands(message)  # Needed to process bot commands

    async def run_agent(self, task: str) -> str:
        # Browser configuration
        config = BrowserConfig(
            headless=True,
            disable_security=True
        )
        browser = Browser(config)

        agent = Agent(
                task=(
                    task
                ),
                llm=self.llm,
                browser=browser
            )

        result = await agent.run()
        return result.history[-1].result[0].extracted_content

    @app_commands.command(name="bu", description="Starts a Browser-Use task.")
    async def bu(self, interaction: discord.Interaction):
        """Handles the /bu slash command."""
        await interaction.response.send_message("Starting browser use task...", ephemeral=True)
         # run browser-use
        await interaction.followup.send(f"Result: is here")
