"""
Email management to enable 2fa.
"""

import asyncio

from agentmail import AsyncAgentMail, Message, MessageReceived, Subscribe
from agentmail.inboxes.types.inbox import Inbox
from agentmail.inboxes.types.inbox_id import InboxId

from browser_use.controller.service import Controller


class EmailController(Controller):
	def __init__(self, email_client: AsyncAgentMail | None = None, email_timeout: int = 30):
		super().__init__()
		self.email_client = email_client or AsyncAgentMail()

		self.email_timeout = email_timeout

		self.register_email_tools()

	def _serialize_message_for_llm(self, message: Message) -> str:
		"""
		Serialize a message for the LLM
		"""
		return f'From: {message.from_}\nTo: {message.to}\nTimestamp: {message.timestamp.isoformat()}\nSubject: {message.subject}\nBody: {message.text}'

	async def get_or_create_inbox_client(self) -> Inbox:
		"""
		Create a default inbox profile for this API key (assume that agent is on free tier)

		If you are not on free tier it is recommended to create 1 inbox per agent.
		"""
		inboxes = await self.email_client.inboxes.list()

		if not inboxes.inboxes:
			inbox = await self.email_client.inboxes.create()
			return inbox

		return inboxes.inboxes[0]

	async def wait_for_message(self, inbox_id: InboxId) -> Message:
		"""
		Wait for a message to be received in the inbox
		"""
		async with self.email_client.websockets.connect() as ws:
			await ws.send_subscribe(message=Subscribe(inbox_ids=[inbox_id]))

			try:
				while True:
					data = await asyncio.wait_for(ws.recv(), timeout=self.email_timeout)
					if isinstance(data, MessageReceived):
						await self.email_client.inboxes.messages.update(
							inbox_id=inbox_id, message_id=data.message.message_id, remove_labels=['unread']
						)
						return data.message
					# If not MessageReceived, continue waiting for the next event
			except TimeoutError:
				raise TimeoutError(f'No email received in the inbox in {self.email_timeout}s')

	def register_email_tools(self):
		"""Register all email-related controller actions"""

		@self.action('Get email address for login. You can use this email to login to any service with email and password')
		async def get_email_address() -> str:
			"""
			Get the email address of the inbox
			"""
			inbox = await self.get_or_create_inbox_client()
			return inbox.inbox_id

		@self.action(
			'Get the latest email from the inbox. You can use this to get the codes for 2fa for example. This function automatically waits for the email to be received.'
		)
		async def get_latest_email() -> str:
			"""
			1. check whether there is an unread email in the inbox; if multiple return all emails as string
			2. if no email; connect via websocket to agentmail and wait until `message_received`
			"""

			inbox = await self.get_or_create_inbox_client()

			emails = await self.email_client.inboxes.messages.list(inbox_id=inbox.inbox_id, labels=['unread'])

			if not emails.messages:
				latest_message = await self.wait_for_message(inbox_id=inbox.inbox_id)
				return self._serialize_message_for_llm(latest_message)

			last_email_id = emails.messages[-1].message_id

			last_email = await self.email_client.inboxes.messages.get(inbox_id=inbox.inbox_id, message_id=last_email_id)
			await self.email_client.inboxes.messages.update(
				inbox_id=inbox.inbox_id, message_id=last_email_id, remove_labels=['unread']
			)

			return self._serialize_message_for_llm(last_email)
