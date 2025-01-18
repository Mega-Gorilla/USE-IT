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
        try:
            print(f'We have logged in as {self.user}')
            cmds = await self.tree.sync() # Sync the command tree with discord
            print(f"Synced commands: {cmds}")
        except Exception as e:
            print(f"Error during bot startup: {e}")

    async def on_message(self, message):
       """Called when a message is received."""
       try:
           if message.author == self.user: # Ignore the bot's messages
                return
           if message.content.strip().startswith("$bu "):
                # try:
                #     await message.reply(
                #         "Starting browser use task...",
                #         mention_author=True  # Don't ping the user
                #     )
                # except Exception as e:
                #     print(f"Error sending start message: {e}")

                try:
                    agent_message = await self.run_agent(message.content.replace("$bu ", "").strip())
                    await message.channel.send(
                            content=f"{agent_message}",
                            reference=message,
                            mention_author=True
                        )
                except Exception as e:
                    await message.channel.send(
                            content=f"Error during task execution: {str(e)}",
                            reference=message,
                            mention_author=True
                        )
                    
       except Exception as e:
           print(f"Error in message handling: {e}")
            
    #    await self.process_commands(message)  # Needed to process bot commands

    async def run_agent(self, task: str) -> str:
        try:
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

            if result.is_done():
                agent_message = result.history[-1].result[0].extracted_content
            else:
                agent_message = "Oops! Something went wrong while running Browser-Use."
            
            return agent_message
        except Exception as e:
            raise Exception(f"Browser-use task failed: {str(e)}")

    # trying to implement slash commands; the following don't seem to work
    # @app_commands.command(name="bu", description="Starts a Browser-Use task.")
    # async def bu(self, interaction: discord.Interaction):
    #     """Handles the /bu slash command."""
    #     try:
    #         await interaction.response.send_message("Starting browser use task...", ephemeral=True)
    #         await interaction.followup.send(f"Result: is here")
    #     except Exception as e:
    #         await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)
