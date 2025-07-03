"""
Gmail Actions for Browser Use
Defines agent actions for Gmail integration including 2FA code retrieval,
email reading, and authentication management.
"""

import logging

from pydantic import BaseModel, Field

from browser_use.agent.views import ActionResult
from browser_use.controller.service import Controller

from .service import GmailService

logger = logging.getLogger(__name__)

# Global Gmail service instance - initialized when actions are registered
_gmail_service: GmailService | None = None


class GetRecentEmailsParams(BaseModel):
	"""Parameters for getting recent emails"""

	query: str = Field(
		default='', description='Gmail search query (e.g., "from:noreply@example.com") - optional additional filter'
	)


def register_gmail_actions(
	controller: Controller, gmail_service: GmailService | None = None, access_token: str | None = None
) -> None:
	"""
	Register Gmail actions with the provided controller
	Args:
	    controller: The browser-use controller to register actions with
	    gmail_service: Optional pre-configured Gmail service instance
	    access_token: Optional direct access token (alternative to file-based auth)
	"""
	global _gmail_service

	# Use provided service or create a new one with access token if provided
	if gmail_service:
		_gmail_service = gmail_service
	elif access_token:
		_gmail_service = GmailService(access_token=access_token)
	else:
		_gmail_service = GmailService()

	@controller.action(
		description='📧 **Get recent verification emails** - Fetches the last 20 emails from the past 5 minutes with full content. '
		'Perfect for finding verification codes, OTP, 2FA, or any recent email content. '
		'Returns complete email content so you can extract verification codes or analyze email details yourself.',
		param_model=GetRecentEmailsParams,
	)
	async def get_recent_emails(params: GetRecentEmailsParams) -> ActionResult:
		"""Get recent emails from the last 5 minutes with full content"""
		try:
			if _gmail_service is None:
				raise RuntimeError('Gmail service not initialized')

			# Ensure authentication
			if not _gmail_service.is_authenticated():
				logger.info('📧 Gmail not authenticated, attempting authentication...')
				authenticated = await _gmail_service.authenticate()
				if not authenticated:
					return ActionResult(
						extracted_content='❌ Failed to authenticate with Gmail. Please ensure Gmail credentials are set up properly.',
						include_in_memory=True,
					)

			# Fixed parameters: 10 emails max, last 5 minutes
			max_results = 10
			time_filter = '5m'

			# Build query with time filter and optional user query
			query_parts = [f'newer_than:{time_filter}']
			if params.query.strip():
				query_parts.append(params.query.strip())

			query = ' '.join(query_parts)
			logger.info(f'🔍 Gmail search query: {query}')

			# Get emails
			emails = await _gmail_service.get_recent_emails(max_results=max_results, query=query, time_filter=time_filter)

			if not emails:
				query_info = f" matching '{params.query}'" if params.query.strip() else ''
				return ActionResult(
					extracted_content=f'📭 No emails found from the last {time_filter}{query_info}', include_in_memory=True
				)

			# Format with full email content
			content = f'📧 Found {len(emails)} recent email{"s" if len(emails) > 1 else ""} from the last {time_filter}:\n\n'

			for i, email in enumerate(emails, 1):
				content += f'📧 Email {i}:\n'
				content += f'From: {email["from"]}\n'
				content += f'Subject: {email["subject"]}\n'
				content += f'Date: {email["date"]}\n'
				content += f'Content:\n{email["body"]}\n'
				content += '-' * 50 + '\n\n'

			return ActionResult(extracted_content=content, include_in_memory=True)

		except Exception as e:
			logger.error(f'Error getting recent emails: {e}')
			return ActionResult(extracted_content=f'❌ Error getting recent emails: {str(e)}', include_in_memory=True)

	@controller.action(
		description='🔧 **Gmail Setup** - Establishes Gmail connection for email access. '
		'Usually auto-handled, but call this if email actions report authentication errors. '
		'Enables automatic retrieval of verification codes and email content.',
	)
	async def authenticate_gmail() -> ActionResult:
		"""Authenticate with Gmail API"""
		try:
			if _gmail_service is None:
				raise RuntimeError('Gmail service not initialized')

			authenticated = await _gmail_service.authenticate()

			if authenticated:
				return ActionResult(
					extracted_content='✅ Successfully authenticated with Gmail API. Ready to read emails.',
					include_in_memory=True,
				)
			else:
				return ActionResult(
					extracted_content='❌ Failed to authenticate with Gmail. Please check your credentials setup:\n'
					'1. Download OAuth credentials from Google Cloud Console\n'
					"2. Save as 'gmail_credentials.json' in ~/.config/browseruse/\n"
					'3. Try again',
					include_in_memory=True,
				)

		except Exception as e:
			logger.error(f'Error authenticating Gmail: {e}')
			return ActionResult(extracted_content=f'❌ Error authenticating with Gmail: {str(e)}', include_in_memory=True)
