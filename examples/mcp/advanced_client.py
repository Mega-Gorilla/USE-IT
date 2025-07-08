"""
Advanced example: Using Gmail MCP Server for automated account verification.

This example demonstrates how to:
1. Connect Gmail MCP Server to browser-use
2. Sign up for a new account on a website
3. Retrieve the verification link from Gmail
4. Complete the verification process
"""

import asyncio
import os

from browser_use import Agent, Controller
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.mcp.client import MCPClient


async def main():
	"""Sign up for Anthropic account and verify via Gmail."""

	# Initialize controller
	controller = Controller()

	# Connect to Gmail MCP Server
	# Requires Gmail API credentials - see: https://github.com/GongRzhe/Gmail-MCP-Server#setup
	# Get Gmail credentials from environment
	gmail_env = {}
	if client_id := os.getenv('GMAIL_CLIENT_ID'):
		gmail_env['GMAIL_CLIENT_ID'] = client_id
	if client_secret := os.getenv('GMAIL_CLIENT_SECRET'):
		gmail_env['GMAIL_CLIENT_SECRET'] = client_secret
	if refresh_token := os.getenv('GMAIL_REFRESH_TOKEN'):
		gmail_env['GMAIL_REFRESH_TOKEN'] = refresh_token

	gmail_client = MCPClient(server_name='gmail', command='npx', args=['gmail-mcp-server'], env=gmail_env)

	# Connect and register Gmail tools
	await gmail_client.connect()
	await gmail_client.register_to_controller(controller)

	# Create agent with extended system prompt for Gmail integration
	agent = Agent(
		task='Sign up for a new Anthropic account using the email example@gmail.com',
		llm=ChatOpenAI(model='gpt-4o'),
		controller=controller,
		system_prompt="""
You are a browser automation agent with access to Gmail tools. When signing up for accounts:

1. Fill out registration forms with the provided email address
2. After submitting the registration, use the Gmail MCP tools to check for verification emails
3. Search for recent emails (within the last 5 minutes) from the service you're signing up for
4. Look for verification links or codes in those emails
5. Use any verification links or codes found to complete the account setup

Available Gmail tools include:
- search_emails: Search for emails by query (e.g., "from:noreply@anthropic.com")
- get_email: Get full email content by ID
- list_emails: List recent emails

Always wait a few seconds after submitting a form before checking Gmail to allow the email to arrive.
""",
	)

	# Run the agent
	result = await agent.run()

	print('\nTask completed!')
	print(f'Result: {result}')

	# Disconnect Gmail client
	await gmail_client.disconnect()


if __name__ == '__main__':
	# Prerequisites:
	# 1. Install Gmail MCP Server: npm install -g gmail-mcp-server
	# 2. Set up Gmail API credentials following: https://github.com/GongRzhe/Gmail-MCP-Server#setup
	# 3. Set these environment variables:
	#    export GMAIL_CLIENT_ID="your-client-id"
	#    export GMAIL_CLIENT_SECRET="your-client-secret"
	#    export GMAIL_REFRESH_TOKEN="your-refresh-token"

	asyncio.run(main())
