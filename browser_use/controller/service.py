import asyncio
import enum
import json
import logging
import re
from typing import Generic, TypeVar

try:
	from lmnr import Laminar  # type: ignore
except ImportError:
	Laminar = None  # type: ignore
from pydantic import BaseModel

from browser_use.agent.views import ActionModel, ActionResult
from browser_use.browser import BrowserSession
from browser_use.browser.events import (
	ClickElementEvent,
	CloseTabEvent,
	GoBackEvent,
	NavigateToUrlEvent,
	ScrollEvent,
	ScrollToTextEvent,
	SendKeysEvent,
	SwitchTabEvent,
	TypeTextEvent,
)
from browser_use.browser.views import BrowserError
from browser_use.controller.registry.service import Registry
from browser_use.controller.views import (
	ClickElementAction,
	CloseTabAction,
	DoneAction,
	GoToUrlAction,
	InputTextAction,
	NoParamsAction,
	ScrollAction,
	SearchGoogleAction,
	SendKeysAction,
	StructuredOutputAction,
	SwitchTabAction,
)
from browser_use.filesystem.file_system import FileSystem
from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import UserMessage
from browser_use.observability import observe_debug
from browser_use.utils import time_execution_sync

logger = logging.getLogger(__name__)

# Import EnhancedDOMTreeNode and rebuild event models that have forward references to it
# This must be done after all imports are complete
ClickElementEvent.model_rebuild()
TypeTextEvent.model_rebuild()
ScrollEvent.model_rebuild()
# Note: UploadFileEvent also has node references but is not imported yet

Context = TypeVar('Context')

T = TypeVar('T', bound=BaseModel)


class Controller(Generic[Context]):
	def __init__(
		self,
		exclude_actions: list[str] = [],
		output_model: type[T] | None = None,
		display_files_in_done_text: bool = True,
	):
		self.registry = Registry[Context](exclude_actions)
		self.display_files_in_done_text = display_files_in_done_text

		"""Register all default browser actions"""

		self._register_done_action(output_model)

		# Basic Navigation Actions
		@self.registry.action(
			'Search the query in Google, the query should be a search query like humans search in Google, concrete and not vague or super long.',
			param_model=SearchGoogleAction,
		)
		async def search_google(params: SearchGoogleAction, browser_session: BrowserSession):
			search_url = f'https://www.google.com/search?q={params.query}&udm=14'

			# Check if there's already a tab open on Google or agent's about:blank
			use_new_tab = True
			try:
				tabs = await browser_session.get_tabs()
				# Get last 4 chars of browser session ID to identify agent's tabs
				browser_session_label = str(browser_session.id)[-4:]
				logger.debug(f'Checking {len(tabs)} tabs for reusable tab (browser_session_label: {browser_session_label})')

				for i, tab in enumerate(tabs):
					logger.debug(f'Tab {i}: url="{tab.url}", title="{tab.title}"')
					# Check if tab is on Google domain
					if tab.url and tab.url.strip('/').lower() in ('https://www.google.com', 'https://google.com'):
						# Found existing Google tab, navigate in it
						logger.debug(f'Found existing Google tab at index {i}: {tab.url}, reusing it')

						# Switch to this tab first if it's not the current one
						from browser_use.browser.events import SwitchTabEvent

						if browser_session.agent_focus and tab.id != browser_session.agent_focus.target_id:
							switch_event = browser_session.event_bus.dispatch(SwitchTabEvent(tab_index=i))
							await switch_event

						use_new_tab = False
						break
					# Check if it's an agent-owned about:blank page (has "Starting agent XXXX..." title)
					# IMPORTANT: about:blank is also used briefly for new tabs the agent is trying to open, dont take over those!
					elif tab.url == 'about:blank' and tab.title:
						# Check if this is our agent's about:blank page with DVD animation
						# The title should be "Starting agent XXXX..." where XXXX is the browser_session_label
						expected_title = f'Starting agent {browser_session_label}...'
						if tab.title == expected_title or browser_session_label in tab.title:
							# This is our agent's about:blank page
							logger.debug(f'Found agent-owned about:blank tab at index {i} with title: "{tab.title}", reusing it')

							# Switch to this tab first
							from browser_use.browser.events import SwitchTabEvent

							if browser_session.agent_focus and tab.id != browser_session.agent_focus.target_id:
								switch_event = browser_session.event_bus.dispatch(SwitchTabEvent(tab_index=i))
								await switch_event

							use_new_tab = False
							break
			except Exception as e:
				logger.debug(f'Could not check for existing tabs: {e}, using new tab')

			# Dispatch navigation event
			event = browser_session.event_bus.dispatch(
				NavigateToUrlEvent(
					url=search_url,
					new_tab=use_new_tab,
				)
			)
			await event

			msg = f'🔍  Searched for "{params.query}" in Google'
			logger.info(msg)
			return ActionResult(
				extracted_content=msg, include_in_memory=True, long_term_memory=f"Searched Google for '{params.query}'"
			)

		@self.registry.action(
			'Navigate to URL, set new_tab=True to open in new tab, False to navigate in current tab', param_model=GoToUrlAction
		)
		async def go_to_url(params: GoToUrlAction, browser_session: BrowserSession):
			try:
				# Dispatch navigation event
				event = browser_session.event_bus.dispatch(NavigateToUrlEvent(url=params.url, new_tab=params.new_tab))
				await event

				if params.new_tab:
					memory = f'Opened new tab with URL {params.url}'
					msg = f'🔗  Opened new tab with url {params.url}'
				else:
					memory = f'Navigated to {params.url}'
					msg = f'🔗 {memory}'

				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=memory)
			except Exception as e:
				error_msg = str(e)
				# Always log the actual error first for debugging
				browser_session.logger.error(f'❌ Navigation failed: {error_msg}')

				# Check if it's specifically a RuntimeError about CDP client
				if isinstance(e, RuntimeError) and 'CDP client not initialized' in error_msg:
					browser_session.logger.error('❌ Browser connection failed - CDP client not properly initialized')
					raise BrowserError(f'Browser connection error: {error_msg}')
				# Check for network-related errors
				elif any(
					err in error_msg
					for err in [
						'ERR_NAME_NOT_RESOLVED',
						'ERR_INTERNET_DISCONNECTED',
						'ERR_CONNECTION_REFUSED',
						'ERR_TIMED_OUT',
						'net::',
					]
				):
					site_unavailable_msg = f'Site unavailable: {params.url} - {error_msg}'
					browser_session.logger.warning(f'⚠️ {site_unavailable_msg}')
					raise BrowserError(site_unavailable_msg)
				else:
					# Re-raise the original error
					raise

		@self.registry.action('Go back', param_model=NoParamsAction)
		async def go_back(_: NoParamsAction, browser_session: BrowserSession):
			try:
				event = browser_session.event_bus.dispatch(GoBackEvent())
				await event
			except Exception as e:
				logger.error(f'Failed to dispatch GoBackEvent: {type(e).__name__}: {e}')
				raise ValueError(f'Failed to go back: {e}') from e
			msg = '🔙  Navigated back'
			logger.info(msg)
			return ActionResult(extracted_content=msg)

		@self.registry.action(
			'Wait for x seconds default 3 (max 10 seconds). This can be used to wait until the page is fully loaded.'
		)
		async def wait(seconds: int = 3):
			# Cap wait time at maximum 10 seconds
			# Reduce the wait time by 3 seconds to account for the llm call which takes at least 3 seconds
			# So if the model decides to wait for 5 seconds, the llm call took at least 3 seconds, so we only need to wait for 2 seconds
			actual_seconds = min(max(seconds - 3, 0), 10)
			msg = f'🕒  Waiting for {actual_seconds + 3} seconds'
			logger.info(msg)
			await asyncio.sleep(actual_seconds)
			return ActionResult(extracted_content=msg)

		# Element Interaction Actions

		@self.registry.action(
			'Click element by index, set new_tab=True to open any resulting navigation in a new tab',
			param_model=ClickElementAction,
		)
		async def click_element_by_index(params: ClickElementAction, browser_session: BrowserSession):
			# Look up the node from the selector map
			node = await browser_session.get_element_by_index(params.index)
			if node is None:
				raise ValueError(f'Element index {params.index} not found in DOM')

			# Dispatch click event with node
			try:
				await browser_session.event_bus.dispatch(ClickElementEvent(node=node, new_tab=params.new_tab))
			except Exception as e:
				logger.error(f'Failed to dispatch ClickElementEvent: {type(e).__name__}: {e}')
				raise ValueError(f'Failed to click element {params.index}: {e}') from e

			msg = f'🖱️ Clicked element with index {params.index}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=msg)

		@self.registry.action(
			'Click and input text into a input interactive element',
			param_model=InputTextAction,
		)
		async def input_text(params: InputTextAction, browser_session: BrowserSession, has_sensitive_data: bool = False):
			# Look up the node from the selector map
			node = await browser_session.get_element_by_index(params.index)
			if node is None:
				raise ValueError(f'Element index {params.index} not found in DOM')

			# Dispatch type text event with node
			try:
				event = browser_session.event_bus.dispatch(TypeTextEvent(node=node, text=params.text))
				await event
			except Exception as e:
				# Log the full error for debugging
				logger.error(f'Failed to dispatch TypeTextEvent: {type(e).__name__}: {e}')
				# Re-raise with more context
				raise ValueError(f'Failed to input text into element {params.index}: {e}') from e

			# AUTO-ENTER for search fields: Automatically press Enter for search inputs
			should_auto_enter = False

			# Check if this is a search field based on element attributes
			if node.attributes:
				# Check for search-related attributes
				search_indicators = {'search', 'query', 'find', 'lookup', 'searchbox', 'search-input'}

				# Check input type
				input_type = node.attributes.get('type', '').lower()
				if input_type == 'search':
					should_auto_enter = True

				# Check class names
				class_list = node.attributes.get('class', '').lower()
				if any(indicator in class_list for indicator in search_indicators):
					should_auto_enter = True

				# Check id
				element_id = node.attributes.get('id', '').lower()
				if any(indicator in element_id for indicator in search_indicators):
					should_auto_enter = True

				# Check placeholder text
				placeholder = node.attributes.get('placeholder', '').lower()
				if any(indicator in placeholder for indicator in search_indicators):
					should_auto_enter = True

				# Check aria-label
				aria_label = node.attributes.get('aria-label', '').lower()
				if any(indicator in aria_label for indicator in search_indicators):
					should_auto_enter = True

			# Auto-press Enter for search fields
			if should_auto_enter:
				try:
					logger.info('🔍 Detected search field, auto-pressing Enter after input')
					enter_event = browser_session.event_bus.dispatch(SendKeysEvent(keys='Enter'))
					await enter_event
				except Exception as e:
					logger.warning(f'Failed to auto-press Enter: {e}')
					# Don't fail the entire action if Enter fails

			if not has_sensitive_data:
				msg = f'⌨️  Input {params.text} into index {params.index}'
				if should_auto_enter:
					msg += ' (+ Enter)'
			else:
				msg = f'⌨️  Input sensitive data into index {params.index}'
				if should_auto_enter:
					msg += ' (+ Enter)'

			logger.info(msg)
			return ActionResult(
				extracted_content=msg,
				include_in_memory=True,
				long_term_memory=f"Input '{params.text}' into element {params.index}."
				+ (' Pressed Enter automatically.' if should_auto_enter else ''),
			)

		# @self.registry.action('Upload file to interactive element with file path', param_model=UploadFileAction)
		# async def upload_file(params: UploadFileAction, browser_session: BrowserSession, available_file_paths: list[str]):
		# 	if params.path not in available_file_paths:
		# 		raise BrowserError(f'File path {params.path} is not available')

		# 	if not os.path.exists(params.path):
		# 		raise BrowserError(f'File {params.path} does not exist')

		# 	# Look up the node from the selector map
		# 	node = EnhancedDOMTreeNode.from_element_index(browser_session, params.index)

		# 	# Dispatch upload file event with node
		# 	event = browser_session.event_bus.dispatch(
		# 		UploadFileEvent(
		# 			node=node,
		# 			file_path=params.path
		# 		)
		# 	)
		# 	await event

		# 	msg = f'📁 Successfully uploaded file to index {params.index}'
		# 	logger.info(msg)
		# 	return ActionResult(
		# 		extracted_content=msg,
		# 		include_in_memory=True,
		# 		long_term_memory=f'Uploaded file {params.path} to element {params.index}',
		# 	)

		# Tab Management Actions

		@self.registry.action('Switch tab', param_model=SwitchTabAction)
		async def switch_tab(params: SwitchTabAction, browser_session: BrowserSession):
			# Dispatch switch tab event
			event = browser_session.event_bus.dispatch(SwitchTabEvent(tab_index=params.page_id))
			await event

			msg = f'🔄  Switched to tab #{params.page_id}'
			logger.info(msg)
			return ActionResult(
				extracted_content=msg, include_in_memory=True, long_term_memory=f'Switched to tab {params.page_id}'
			)

		@self.registry.action('Close an existing tab', param_model=CloseTabAction)
		async def close_tab(params: CloseTabAction, browser_session: BrowserSession):
			# Dispatch close tab event
			event = browser_session.event_bus.dispatch(CloseTabEvent(tab_index=params.page_id))
			await event

			msg = f'❌  Closed tab #{params.page_id}'
			logger.info(msg)
			return ActionResult(
				extracted_content=msg,
				include_in_memory=True,
				long_term_memory=f'Closed tab {params.page_id}',
			)

		# Content Actions

		# TODO: Refactor to use events instead of direct page access
		# This action is temporarily disabled as it needs refactoring to use events

		@self.registry.action(
			"""Extract structured, semantic data (e.g. product description, price, all information about XYZ) from the current webpage based on a textual query.
		This tool takes the entire markdown of the page and extracts the query from it.
		Set extract_links=True ONLY if your query requires extracting links/URLs from the page.
		Only use this for specific queries for information retrieval from the page. Don't use this to get interactive elements - the tool does not see HTML elements, only the markdown.
		""",
		)
		async def extract_structured_data(
			query: str,
			extract_links: bool,
			browser_session: BrowserSession,
			page_extraction_llm: BaseChatModel,
			file_system: FileSystem,
		):
			loop = asyncio.get_event_loop()

			cdp_session = await browser_session.get_or_create_cdp_session()

			try:
				# Get the HTML content
				body_id = await cdp_session.cdp_client.send.DOM.getDocument(session_id=cdp_session.session_id)
				page_html_result = await cdp_session.cdp_client.send.DOM.getOuterHTML(
					params={'backendNodeId': body_id['root']['backendNodeId']}, session_id=cdp_session.session_id
				)
			except TimeoutError:
				raise RuntimeError('Page content extraction timed out after 5 seconds')
			except Exception as e:
				raise RuntimeError(f"Couldn't extract page content: {e}")

			page_html = page_html_result['outerHTML']

			try:
				# Clean HTML before processing
				page_html = self._clean_html_for_extraction(page_html)
			except Exception as e:
				logger.warning(f'Error cleaning HTML: {type(e).__name__}: {e}')

			# Convert to markdown with custom processing
			try:
				content = await asyncio.wait_for(
					loop.run_in_executor(None, self._html_to_clean_markdown, page_html, extract_links), timeout=10.0
				)
			except Exception as e:
				logger.warning(f'Markdown conversion failed: {type(e).__name__}')
				raise RuntimeError(f'Could not convert html to markdown: {type(e).__name__}')

			# Clean and validate content
			content = self._clean_and_validate_content(content, query)

			# Check if content is relevant to query
			if not self._is_content_relevant(content, query):
				error_response = {
					'status': 'information_not_available',
					'explanation': f"The webpage does not contain information relevant to the query: '{query}'. The page appears to contain different content.",
					'page_content_summary': content[:200] + '...' if len(content) > 200 else content,
					'requested_information': None,
				}
				extracted_content = (
					f'Page Link: {cdp_session.url}\nQuery: {query}\nExtracted Content:\n{json.dumps(error_response, indent=2)}'
				)
				memory = f'Content extraction failed - query "{query}" not relevant to page content'
				logger.info(f'📄 {memory}')
				return ActionResult(
					extracted_content=extracted_content,
					include_extracted_content_only_once=False,
					long_term_memory=memory,
				)

			# Limit content length intelligently
			if len(content) > 25000:
				content = self._smart_truncate_content(content, 25000)

			# Improved prompt for better extraction
			prompt = """You are an expert content extractor. Extract precise, structured information from this webpage based on the query.

**Instructions:**
1. Focus ONLY on content directly relevant to the query
2. If the information is not available, clearly state so
3. Provide structured, accurate information in JSON format
4. Include specific details, numbers, quotes when available
5. If extracting lists, maintain the original order

**Query:** {query}

**Webpage Content:**
{page}

**Response Format:** Provide a clean JSON response with the extracted information."""

			try:
				formatted_prompt = prompt.format(query=query, page=content)
				response = await asyncio.wait_for(
					page_extraction_llm.ainvoke([UserMessage(content=formatted_prompt)]),
					timeout=120.0,
				)

				# Clean the response
				cleaned_response = self._clean_llm_response(response.completion)
				extracted_content = f'Page Link: {cdp_session.url}\nQuery: {query}\nExtracted Content:\n{cleaned_response}'

				# Optimize memory usage
				MAX_MEMORY_SIZE = 800
				if len(extracted_content) < MAX_MEMORY_SIZE:
					memory = extracted_content
					include_extracted_content_only_once = False
				else:
					save_result = await file_system.save_extracted_content(extracted_content)
					# Create concise memory summary
					try:
						response_preview = json.loads(cleaned_response)
						if isinstance(response_preview, dict):
							summary_keys = list(response_preview.keys())[:3]
							summary = {k: response_preview[k] for k in summary_keys}
							summary_text = json.dumps(summary, indent=2)[:400]
						else:
							summary_text = str(response_preview)[:400]
					except:
						summary_text = cleaned_response[:400]

					memory = f'Extracted content from {cdp_session.url}\n<query>{query}</query>\n<preview>\n{summary_text}...\n</preview>\n<file_system>{save_result}</file_system>'
					include_extracted_content_only_once = True

				logger.info(f'📄 {memory}')
				return ActionResult(
					extracted_content=extracted_content,
					include_extracted_content_only_once=include_extracted_content_only_once,
					long_term_memory=memory,
				)
			except TimeoutError:
				error_msg = f'LLM call timed out for query: {query}'
				logger.warning(error_msg)
				raise RuntimeError(error_msg)
			except Exception as e:
				logger.debug(f'Error extracting content: {e}')
				msg = f'📄 Extraction failed for query: {query}'
				logger.info(msg)
				raise RuntimeError(str(e))

		@self.registry.action(
			'Scroll the page by specified number of pages (set down=True to scroll down, down=False to scroll up, num_pages=number of pages to scroll like 0.5 for half page, 1.0 for one page, etc.). Optional index parameter to scroll within a specific element or its scroll container (works well for dropdowns and custom UI components). Use index=0 or omit index to scroll the entire page.',
			param_model=ScrollAction,
		)
		async def scroll(params: ScrollAction, browser_session: BrowserSession):
			# Look up the node from the selector map if index is provided
			# Special case: index 0 means scroll the whole page (root/body element)
			node = None
			if params.index is not None and params.index != 0:
				try:
					node = await browser_session.get_element_by_index(params.index)
					if node is None:
						# Element not found - return error
						raise ValueError(f'Element index {params.index} not found in DOM')
				except Exception as e:
					# Error getting element - return error
					raise ValueError(f'Failed to get element {params.index}: {e}') from e

			# Dispatch scroll event with node - the complex logic is handled in the event handler
			# Convert pages to pixels (assuming 800px per page as standard viewport height)
			pixels = int(params.num_pages * 800)
			try:
				event = browser_session.event_bus.dispatch(
					ScrollEvent(direction='down' if params.down else 'up', amount=pixels, node=node)
				)
				await event
			except Exception as e:
				logger.error(f'Failed to dispatch ScrollEvent: {type(e).__name__}: {e}')
				raise ValueError(f'Failed to scroll: {e}') from e

			direction = 'down' if params.down else 'up'
			# If index is 0 or None, we're scrolling the page
			target = 'the page' if params.index is None or params.index == 0 else f'element {params.index}'

			if params.num_pages == 1.0:
				long_term_memory = f'Scrolled {direction} {target} by one page'
			else:
				long_term_memory = f'Scrolled {direction} {target} by {params.num_pages} pages'

			msg = f'🔍 {long_term_memory}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=long_term_memory)

		@self.registry.action(
			'Send strings of special keys to use Playwright page.keyboard.press - examples include Escape, Backspace, Insert, PageDown, Delete, Enter, or Shortcuts such as `Control+o`, `Control+Shift+T`',
			param_model=SendKeysAction,
		)
		async def send_keys(params: SendKeysAction, browser_session: BrowserSession):
			# Dispatch send keys event
			try:
				event = browser_session.event_bus.dispatch(SendKeysEvent(keys=params.keys))
				await event
			except Exception as e:
				logger.error(f'Failed to dispatch SendKeysEvent: {type(e).__name__}: {e}')
				raise ValueError(f'Failed to send keys: {e}') from e

			msg = f'⌨️  Sent keys: {params.keys}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=f'Sent keys: {params.keys}')

		@self.registry.action(
			description='Scroll to a text in the current page',
		)
		async def scroll_to_text(text: str, browser_session: BrowserSession):  # type: ignore
			# Dispatch scroll to text event
			event = browser_session.event_bus.dispatch(ScrollToTextEvent(text=text))
			await event

			# Check result to see if text was found
			result = await event.event_result()
			if result and result.get('found'):
				msg = f'🔍  Scrolled to text: {text}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=f'Scrolled to text: {text}')
			else:
				msg = f"Text '{text}' not found or not visible on page"
				logger.info(msg)
				return ActionResult(
					extracted_content=msg,
					include_in_memory=True,
					long_term_memory=f"Tried scrolling to text '{text}' but it was not found",
				)

		# File System Actions
		@self.registry.action(
			'Write or append content to file_name in file system. Allowed extensions are .md, .txt, .json, .csv, .pdf. For .pdf files, write the content in markdown format and it will automatically be converted to a properly formatted PDF document.'
		)
		async def write_file(
			file_name: str,
			content: str,
			file_system: FileSystem,
			append: bool = False,
			trailing_newline: bool = True,
			leading_newline: bool = False,
		):
			if trailing_newline:
				content += '\n'
			if leading_newline:
				content = '\n' + content
			if append:
				result = await file_system.append_file(file_name, content)
			else:
				result = await file_system.write_file(file_name, content)
			logger.info(f'💾 {result}')
			return ActionResult(extracted_content=result, include_in_memory=True, long_term_memory=result)

		@self.registry.action(
			'Replace old_str with new_str in file_name. old_str must exactly match the string to replace in original text. Recommended tool to mark completed items in todo.md or change specific contents in a file.'
		)
		async def replace_file_str(file_name: str, old_str: str, new_str: str, file_system: FileSystem):
			result = await file_system.replace_file_str(file_name, old_str, new_str)
			logger.info(f'💾 {result}')
			return ActionResult(extracted_content=result, include_in_memory=True, long_term_memory=result)

		@self.registry.action('Read file_name from file system')
		async def read_file(file_name: str, available_file_paths: list[str], file_system: FileSystem):
			if available_file_paths and file_name in available_file_paths:
				result = await file_system.read_file(file_name, external_file=True)
			else:
				result = await file_system.read_file(file_name)

			MAX_MEMORY_SIZE = 1000
			if len(result) > MAX_MEMORY_SIZE:
				lines = result.splitlines()
				display = ''
				lines_count = 0
				for line in lines:
					if len(display) + len(line) < MAX_MEMORY_SIZE:
						display += line + '\n'
						lines_count += 1
					else:
						break
				remaining_lines = len(lines) - lines_count
				memory = f'{display}{remaining_lines} more lines...' if remaining_lines > 0 else display
			else:
				memory = result
			logger.info(f'💾 {memory}')
			return ActionResult(
				extracted_content=result,
				include_in_memory=True,
				long_term_memory=memory,
				include_extracted_content_only_once=True,
			)

	# TODO: Refactor to use events instead of direct page/dom access
	# @self.registry.action(
	# 	description='Get all options from a native dropdown or ARIA menu',
	# )
	# async def get_dropdown_options(index: int, browser_session: BrowserSession) -> ActionResult:
	# 	"""Get all options from a native dropdown or ARIA menu"""

	# 	dom_element = await browser_session.get_dom_element_by_index(index)
	# 	if dom_element is None:
	# 		raise Exception(f'Element index {index} does not exist - retry or use alternative actions')

	# 	try:
	# 		# Frame-aware approach since we know it works
	# 		all_options = []
	# 		frame_index = 0

	# 		for frame in page.frames:
	# 			try:
	# 				# First check if it's a native select element
	# 				options = await frame.evaluate(
	# 					"""
	# 					(xpath) => {
	# 						const element = document.evaluate(xpath, document, null,
	# 							XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
	# 						if (!element) return null;

	# 						// Check if it's a native select element
	# 						if (element.tagName.toLowerCase() === 'select') {
	# 							return {
	# 								type: 'select',
	# 								options: Array.from(element.options).map(opt => ({
	# 									text: opt.text, //do not trim, because we are doing exact match in select_dropdown_option
	# 									value: opt.value,
	# 									index: opt.index
	# 								})),
	# 								id: element.id,
	# 								name: element.name
	# 							};
	# 						}

	# 						// Check if it's an ARIA menu
	# 						if (element.getAttribute('role') === 'menu' ||
	# 							element.getAttribute('role') === 'listbox' ||
	# 							element.getAttribute('role') === 'combobox') {
	# 							// Find all menu items
	# 							const menuItems = element.querySelectorAll('[role="menuitem"], [role="option"]');
	# 							const options = [];

	# 							menuItems.forEach((item, idx) => {
	# 								// Get the text content of the menu item
	# 								const text = item.textContent.trim();
	# 								if (text) {
	# 									options.push({
	# 										text: text,
	# 										value: text, // For ARIA menus, use text as value
	# 										index: idx
	# 									});
	# 								}
	# 							});

	# 							return {
	# 								type: 'aria',
	# 								options: options,
	# 								id: element.id || '',
	# 								name: element.getAttribute('aria-label') || ''
	# 							};
	# 						}

	# 						return null;
	# 					}
	# 				""",
	# 					dom_element.xpath,
	# 				)

	# 				if options:
	# 					logger.debug(f'Found {options["type"]} dropdown in frame {frame_index}')
	# 					logger.debug(f'Element ID: {options["id"]}, Name: {options["name"]}')

	# 					formatted_options = []
	# 					for opt in options['options']:
	# 						# encoding ensures AI uses the exact string in select_dropdown_option
	# 						encoded_text = json.dumps(opt['text'])
	# 						formatted_options.append(f'{opt["index"]}: text={encoded_text}')

	# 					all_options.extend(formatted_options)

	# 			except Exception as frame_e:
	# 				logger.debug(f'Frame {frame_index} evaluation failed: {str(frame_e)}')

	# 			frame_index += 1

	# 		if all_options:
	# 			msg = '\n'.join(all_options)
	# 			msg += '\nUse the exact text string in select_dropdown_option'
	# 			logger.info(msg)
	# 			return ActionResult(
	# 				extracted_content=msg,
	# 				include_in_memory=True,
	# 				long_term_memory=f'Found dropdown options for index {index}.',
	# 				include_extracted_content_only_once=True,
	# 			)
	# 		else:
	# 			msg = 'No options found in any frame for dropdown'
	# 			logger.info(msg)
	# 			return ActionResult(
	# 				extracted_content=msg, include_in_memory=True, long_term_memory='No dropdown options found'
	# 			)

	# 	except Exception as e:
	# 		logger.error(f'Failed to get dropdown options: {str(e)}')
	# 		msg = f'Error getting options: {str(e)}'
	# 		logger.info(msg)
	# 		return ActionResult(extracted_content=msg, include_in_memory=True)

	# TODO: Refactor to use events instead of direct page/dom access
	# @self.registry.action(
	# 	description='Select dropdown option or ARIA menu item for interactive element index by the text of the option you want to select',
	# )
	# async def select_dropdown_option(
	# 	index: int,
	# 	text: str,
	# 	browser_session: BrowserSession,
	# ) -> ActionResult:
	# 	"""Select dropdown option or ARIA menu item by the text of the option you want to select"""
	# 	page = await browser_session.get_current_page()
	# 	dom_element = await browser_session.get_dom_element_by_index(index)
	# 	if dom_element is None:
	# 		raise Exception(f'Element index {index} does not exist - retry or use alternative actions')

	# 	logger.debug(f"Attempting to select '{text}' using xpath: {dom_element.xpath}")
	# 	logger.debug(f'Element attributes: {dom_element.attributes}')
	# 	logger.debug(f'Element tag: {dom_element.tag_name}')

	# 	xpath = '//' + dom_element.xpath

	# 	try:
	# 		frame_index = 0
	# 		for frame in page.frames:
	# 			try:
	# 				logger.debug(f'Trying frame {frame_index} URL: {frame.url}')

	# 				# First check what type of element we're dealing with
	# 				element_info_js = """
	# 					(xpath) => {
	# 						try {
	# 							const element = document.evaluate(xpath, document, null,
	# 								XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
	# 							if (!element) return null;

	# 							const tagName = element.tagName.toLowerCase();
	# 							const role = element.getAttribute('role');

	# 							// Check if it's a native select
	# 							if (tagName === 'select') {
	# 								return {
	# 									type: 'select',
	# 									found: true,
	# 									id: element.id,
	# 									name: element.name,
	# 									tagName: element.tagName,
	# 									optionCount: element.options.length,
	# 									currentValue: element.value,
	# 									availableOptions: Array.from(element.options).map(o => o.text.trim())
	# 								};
	# 							}

	# 							// Check if it's an ARIA menu or similar
	# 							if (role === 'menu' || role === 'listbox' || role === 'combobox') {
	# 								const menuItems = element.querySelectorAll('[role="menuitem"], [role="option"]');
	# 								return {
	# 									type: 'aria',
	# 									found: true,
	# 									id: element.id || '',
	# 									role: role,
	# 									tagName: element.tagName,
	# 									itemCount: menuItems.length,
	# 									availableOptions: Array.from(menuItems).map(item => item.textContent.trim())
	# 								};
	# 							}

	# 							return {
	# 								error: `Element is neither a select nor an ARIA menu (tag: ${tagName}, role: ${role})`,
	# 								found: false
	# 							};
	# 						} catch (e) {
	# 							return {error: e.toString(), found: false};
	# 						}
	# 					}
	# 				"""

	# 				element_info = await frame.evaluate(element_info_js, dom_element.xpath)

	# 				if element_info and element_info.get('found'):
	# 					logger.debug(f'Found {element_info.get("type")} element in frame {frame_index}: {element_info}')

	# 					if element_info.get('type') == 'select':
	# 						# Handle native select element
	# 						# "label" because we are selecting by text
	# 						# nth(0) to disable error thrown by strict mode
	# 						# timeout=1000 because we are already waiting for all network events
	# 						selected_option_values = (
	# 							await frame.locator('//' + dom_element.xpath).nth(0).select_option(label=text, timeout=1000)
	# 						)

	# 						msg = f'selected option {text} with value {selected_option_values}'
	# 						logger.info(msg + f' in frame {frame_index}')

	# 						return ActionResult(
	# 							extracted_content=msg, include_in_memory=True, long_term_memory=f"Selected option '{text}'"
	# 						)

	# 					elif element_info.get('type') == 'aria':
	# 						# Handle ARIA menu
	# 						click_aria_item_js = """
	# 							(params) => {
	# 								const { xpath, targetText } = params;
	# 								try {
	# 									const element = document.evaluate(xpath, document, null,
	# 										XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
	# 									if (!element) return {success: false, error: 'Element not found'};

	# 									// Find all menu items
	# 									const menuItems = element.querySelectorAll('[role="menuitem"], [role="option"]');

	# 									for (const item of menuItems) {
	# 										const itemText = item.textContent.trim();
	# 										if (itemText === targetText) {
	# 											// Simulate click on the menu item
	# 											item.click();

	# 											// Also try dispatching a click event in case the click handler needs it
	# 											const clickEvent = new MouseEvent('click', {
	# 												view: window,
	# 												bubbles: true,
	# 												cancelable: true
	# 											});
	# 											item.dispatchEvent(clickEvent);

	# 											return {
	# 												success: true,
	# 												message: `Clicked menu item: ${targetText}`
	# 											};
	# 										}
	# 									}

	# 									return {
	# 										success: false,
	# 										error: `Menu item with text '${targetText}' not found`
	# 									};
	# 								} catch (e) {
	# 									return {success: false, error: e.toString()};
	# 								}
	# 							}
	# 						"""

	# 						result = await frame.evaluate(
	# 							click_aria_item_js, {'xpath': dom_element.xpath, 'targetText': text}
	# 						)

	# 						if result.get('success'):
	# 							msg = result.get('message', f'Selected ARIA menu item: {text}')
	# 							logger.info(msg + f' in frame {frame_index}')
	# 							return ActionResult(
	# 								extracted_content=msg,
	# 								include_in_memory=True,
	# 								long_term_memory=f"Selected menu item '{text}'",
	# 							)
	# 						else:
	# 							logger.error(f'Failed to select ARIA menu item: {result.get("error")}')
	# 							continue

	# 				elif element_info:
	# 					logger.error(f'Frame {frame_index} error: {element_info.get("error")}')
	# 					continue

	# 			except Exception as frame_e:
	# 				logger.error(f'Frame {frame_index} attempt failed: {str(frame_e)}')
	# 				logger.error(f'Frame type: {type(frame)}')
	# 				logger.error(f'Frame URL: {frame.url}')

	# 			frame_index += 1

	# 		msg = f"Could not select option '{text}' in any frame"
	# 		logger.info(msg)
	# 		return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=msg)

	# 	except Exception as e:
	# 		msg = f'Selection failed: {str(e)}'
	# 		logger.error(msg)
	# 		raise BrowserError(msg)

	# @self.registry.action('Google Sheets: Get the contents of the entire sheet', domains=['https://docs.google.com'])
	# async def read_sheet_contents(browser_session: BrowserSession):
	# 	# Use send keys events to select and copy all cells
	# 	for key in ['Enter', 'Escape', 'ControlOrMeta+A', 'ControlOrMeta+C']:
	# 		event = browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
	# 		await event

	# 	# Get page to evaluate clipboard
	# 	page = await browser_session.get_current_page()
	# 	extracted_tsv = await page.evaluate('() => navigator.clipboard.readText()')
	# 	return ActionResult(
	# 		extracted_content=extracted_tsv,
	# 		include_in_memory=True,
	# 		long_term_memory='Retrieved sheet contents',
	# 		include_extracted_content_only_once=True,
	# 	)

	# @self.registry.action('Google Sheets: Get the contents of a cell or range of cells', domains=['https://docs.google.com'])
	# async def read_cell_contents(cell_or_range: str, browser_session: BrowserSession):
	# 	page = await browser_session.get_current_page()

	# 	await select_cell_or_range(cell_or_range=cell_or_range, page=page)

	# 	await page.keyboard.press('ControlOrMeta+C')
	# 	await asyncio.sleep(0.1)
	# 	extracted_tsv = await page.evaluate('() => navigator.clipboard.readText()')
	# 	return ActionResult(
	# 		extracted_content=extracted_tsv,
	# 		include_in_memory=True,
	# 		long_term_memory=f'Retrieved contents from {cell_or_range}',
	# 		include_extracted_content_only_once=True,
	# 	)

	# @self.registry.action(
	# 	'Google Sheets: Update the content of a cell or range of cells', domains=['https://docs.google.com']
	# )
	# async def update_cell_contents(cell_or_range: str, new_contents_tsv: str, browser_session: BrowserSession):
	# 	page = await browser_session.get_current_page()

	# 	await select_cell_or_range(cell_or_range=cell_or_range, page=page)

	# 	# simulate paste event from clipboard with TSV content
	# 	await page.evaluate(f"""
	# 		const clipboardData = new DataTransfer();
	# 		clipboardData.setData('text/plain', `{new_contents_tsv}`);
	# 		document.activeElement.dispatchEvent(new ClipboardEvent('paste', {{clipboardData}}));
	# 	""")

	# 	return ActionResult(
	# 		extracted_content=f'Updated cells: {cell_or_range} = {new_contents_tsv}',
	# 		include_in_memory=False,
	# 		long_term_memory=f'Updated cells {cell_or_range} with {new_contents_tsv}',
	# 	)

	# @self.registry.action('Google Sheets: Clear whatever cells are currently selected', domains=['https://docs.google.com'])
	# async def clear_cell_contents(cell_or_range: str, browser_session: BrowserSession):
	# 	page = await browser_session.get_current_page()

	# 	await select_cell_or_range(cell_or_range=cell_or_range, page=page)

	# 	await page.keyboard.press('Backspace')
	# 	return ActionResult(
	# 		extracted_content=f'Cleared cells: {cell_or_range}',
	# 		include_in_memory=False,
	# 		long_term_memory=f'Cleared cells {cell_or_range}',
	# 	)

	# @self.registry.action('Google Sheets: Select a specific cell or range of cells', domains=['https://docs.google.com'])
	# async def select_cell_or_range(cell_or_range: str, browser_session: BrowserSession):
	# 	# Use send keys events for navigation
	# 	for key in ['Enter', 'Escape']:
	# 		event = browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
	# 		await event
	# 	await asyncio.sleep(0.1)
	# 	for key in ['Home', 'ArrowUp']:
	# 		event = browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
	# 		await event
	# 	await asyncio.sleep(0.1)
	# 	event = browser_session.event_bus.dispatch(SendKeysEvent(keys='Control+G'))
	# 	await event
	# 	await asyncio.sleep(0.2)
	# 	# Get page to type the cell range
	# 	page = await browser_session.get_current_page()
	# 	await page.keyboard.type(cell_or_range, delay=0.05)
	# 	await asyncio.sleep(0.2)
	# 	for key in ['Enter', 'Escape']:
	# 		event = browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
	# 		await event
	# 		await asyncio.sleep(0.2)
	# 	return ActionResult(
	# 		extracted_content=f'Selected cells: {cell_or_range}',
	# 		include_in_memory=False,
	# 		long_term_memory=f'Selected cells {cell_or_range}',
	# 	)

	# @self.registry.action(
	# 	'Google Sheets: Fallback method to type text into (only one) currently selected cell',
	# 	domains=['https://docs.google.com'],
	# )
	# async def fallback_input_into_single_selected_cell(text: str, browser_session: BrowserSession):
	# 	# Get page to type text
	# 	page = await browser_session.get_current_page()
	# 	await page.keyboard.type(text, delay=0.1)
	# 	# Use send keys for Enter and ArrowUp
	# 	for key in ['Enter', 'ArrowUp']:
	# 		event = browser_session.event_bus.dispatch(SendKeysEvent(keys=key))
	# 		await event
	# 	return ActionResult(
	# 		extracted_content=f'Inputted text {text}',
	# 		include_in_memory=False,
	# 		long_term_memory=f"Inputted text '{text}' into cell",
	# 	)

	# Custom done action for structured output
	def _register_done_action(self, output_model: type[T] | None, display_files_in_done_text: bool = True):
		if output_model is not None:
			self.display_files_in_done_text = display_files_in_done_text

			@self.registry.action(
				'Complete task - with return text and if the task is finished (success=True) or not yet completely finished (success=False), because last step is reached',
				param_model=StructuredOutputAction[output_model],
			)
			async def done(params: StructuredOutputAction):
				# Exclude success from the output JSON since it's an internal parameter
				output_dict = params.data.model_dump()

				# Enums are not serializable, convert to string
				for key, value in output_dict.items():
					if isinstance(value, enum.Enum):
						output_dict[key] = value.value

				return ActionResult(
					is_done=True,
					success=params.success,
					extracted_content=json.dumps(output_dict),
					long_term_memory=f'Task completed. Success Status: {params.success}',
				)

		else:

			@self.registry.action(
				'Complete task - provide a summary of results for the user. Set success=True if task completed successfully, false otherwise. Text should be your response to the user summarizing results. Include files you would like to display to the user in files_to_display.',
				param_model=DoneAction,
			)
			async def done(params: DoneAction, file_system: FileSystem):
				user_message = params.text

				len_text = len(params.text)
				len_max_memory = 100
				memory = f'Task completed: {params.success} - {params.text[:len_max_memory]}'
				if len_text > len_max_memory:
					memory += f' - {len_text - len_max_memory} more characters'

				attachments = []
				if params.files_to_display:
					if self.display_files_in_done_text:
						file_msg = ''
						for file_name in params.files_to_display:
							if file_name == 'todo.md':
								continue
							file_content = file_system.display_file(file_name)
							if file_content:
								file_msg += f'\n\n{file_name}:\n{file_content}'
								attachments.append(file_name)
						if file_msg:
							user_message += '\n\nAttachments:'
							user_message += file_msg
						else:
							logger.warning('Agent wanted to display files but none were found')
					else:
						for file_name in params.files_to_display:
							if file_name == 'todo.md':
								continue
							file_content = file_system.display_file(file_name)
							if file_content:
								attachments.append(file_name)

				attachments = [str(file_system.get_dir() / file_name) for file_name in attachments]

				return ActionResult(
					is_done=True,
					success=params.success,
					extracted_content=user_message,
					long_term_memory=memory,
					attachments=attachments,
				)

	def use_structured_output_action(self, output_model: type[T]):
		self._register_done_action(output_model)

	# Register ---------------------------------------------------------------

	def action(self, description: str, **kwargs):
		"""Decorator for registering custom actions

		@param description: Describe the LLM what the function does (better description == better function calling)
		"""
		return self.registry.action(description, **kwargs)

	# Act --------------------------------------------------------------------
	@observe_debug(ignore_input=True, ignore_output=True, name='act')
	@time_execution_sync('--act')
	async def act(
		self,
		action: ActionModel,
		browser_session: BrowserSession,
		#
		page_extraction_llm: BaseChatModel | None = None,
		sensitive_data: dict[str, str | dict[str, str]] | None = None,
		available_file_paths: list[str] | None = None,
		file_system: FileSystem | None = None,
		#
		context: Context | None = None,
	) -> ActionResult:
		"""Execute an action"""

		for action_name, params in action.model_dump(exclude_unset=True).items():
			if params is not None:
				# Use Laminar span if available, otherwise use no-op context manager
				if Laminar is not None:
					span_context = Laminar.start_as_current_span(
						name=action_name,
						input={
							'action': action_name,
							'params': params,
						},
						span_type='TOOL',
					)
				else:
					# No-op context manager when lmnr is not available
					from contextlib import nullcontext

					span_context = nullcontext()

				with span_context:
					try:
						result = await self.registry.execute_action(
							action_name=action_name,
							params=params,
							browser_session=browser_session,
							page_extraction_llm=page_extraction_llm,
							file_system=file_system,
							sensitive_data=sensitive_data,
							available_file_paths=available_file_paths,
							context=context,
						)
					except Exception as e:
						result = ActionResult(error=str(e))

					if Laminar is not None:
						Laminar.set_span_output(result)

				if isinstance(result, str):
					return ActionResult(extracted_content=result)
				elif isinstance(result, ActionResult):
					return result
				elif result is None:
					return ActionResult()
				else:
					raise ValueError(f'Invalid action result type: {type(result)} of {result}')
		return ActionResult()

	def _clean_html_for_extraction(self, html: str) -> str:
		"""Clean HTML by removing unnecessary elements and attributes."""

		# Remove script and style elements
		html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)

		# Remove navigation and header elements
		html = re.sub(r'<(nav|header|footer)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)

		# Remove debugging elements (browser-use highlighting)
		html = re.sub(r'<[^>]*data-browser-use[^>]*>.*?</[^>]*>', '', html, flags=re.DOTALL)
		html = re.sub(r'<[^>]*browser-use[^>]*>', '', html, flags=re.IGNORECASE)

		# Remove data URLs and base64 content
		html = re.sub(r'data:[^;\s,]+;[^,\s]*,([^"\'\s>]+)', '', html, flags=re.IGNORECASE)

		# Remove style attributes with positioning
		html = re.sub(r'style\s*=\s*["\'][^"\']*position[^"\']*["\']', '', html, flags=re.IGNORECASE)

		# Remove excessive whitespace
		html = re.sub(r'\s+', ' ', html)

		return html

	def _html_to_clean_markdown(self, html: str, extract_links: bool = False) -> str:
		"""Convert HTML to clean, semantic markdown."""
		import markdownify

		# Configure markdownify for cleaner output
		if extract_links:
			# Keep links when requested
			content = markdownify.markdownify(
				html, heading_style='ATX', bullets='-', strip=['script', 'style', 'meta', 'link', 'title', 'head']
			)
		else:
			# Remove links for cleaner text-only content
			content = markdownify.markdownify(
				html, heading_style='ATX', bullets='-', strip=['script', 'style', 'meta', 'link', 'title', 'head', 'a']
			)

		return content

	def _clean_and_validate_content(self, content: str, query: str) -> str:
		"""Clean and validate the markdown content."""

		# Remove excessive newlines
		content = re.sub(r'\n{3,}', '\n\n', content)

		# Remove browser-use debug elements
		content = re.sub(r'❓\s*\[\d+\]\s*\w+.*?UNKNOWN CONFIDENCE.*?(?=\n|$)', '', content, flags=re.MULTILINE)
		content = re.sub(r'Primary:\s*UNKNOWN.*?(?=\n|$)', '', content, flags=re.MULTILINE)
		content = re.sub(r'No specific evidence found.*?(?=\n|$)', '', content, flags=re.MULTILINE)
		content = re.sub(r'Position:\s*\([^)]+\)\s*Size:\s*[^\n]*', '', content, flags=re.MULTILINE)

		# Remove navigation elements
		content = re.sub(r'(Home|About|Contact|Menu|Navigation|Login|Signin|Signup)[\|\s]*', '', content, flags=re.IGNORECASE)

		# Remove common web elements
		content = re.sub(r'(Cookie|Privacy Policy|Terms|Accept|Decline)[\|\s]*', '', content, flags=re.IGNORECASE)

		# Clean up remaining artifacts
		content = re.sub(r'^\s*[-=_|]+\s*$', '', content, flags=re.MULTILINE)  # Remove separator lines
		content = re.sub(r'^\s*\d+\s*$', '', content, flags=re.MULTILINE)  # Remove standalone numbers

		# Final cleanup
		content = re.sub(r'\n{3,}', '\n\n', content)
		content = content.strip()

		return content

	def _is_content_relevant(self, content: str, query: str) -> bool:
		"""Check if content contains information relevant to the query."""
		if len(content.strip()) < 50:  # Too little content
			return False

		# Extract key terms from query
		query_words = set(query.lower().split())
		content_lower = content.lower()

		# Remove common stop words from query
		stop_words = {
			'the',
			'a',
			'an',
			'and',
			'or',
			'but',
			'in',
			'on',
			'at',
			'to',
			'for',
			'of',
			'with',
			'by',
			'what',
			'how',
			'when',
			'where',
			'why',
			'who',
		}
		significant_words = query_words - stop_words

		if len(significant_words) == 0:
			return True  # Query too generic, assume relevant

		# Check if at least some query terms appear in content
		matches = sum(1 for word in significant_words if word in content_lower)
		relevance_ratio = matches / len(significant_words)

		return relevance_ratio >= 0.3  # At least 30% of significant query words should appear

	def _smart_truncate_content(self, content: str, max_chars: int) -> str:
		"""Intelligently truncate content while preserving structure."""
		if len(content) <= max_chars:
			return content

		# Try to truncate at paragraph boundaries
		paragraphs = content.split('\n\n')
		truncated = ''

		for para in paragraphs:
			if len(truncated) + len(para) + 2 <= max_chars - 100:  # Leave room for truncation message
				truncated += para + '\n\n'
			else:
				break

		if len(truncated) < max_chars // 2:  # If we lost too much, use simple truncation
			truncated = content[: max_chars - 100]

		truncated += '\n\n... [Content truncated for length - full content available in extracted file] ...'
		return truncated

	def _clean_llm_response(self, response: str) -> str:
		"""Clean and validate LLM response."""
		import json

		# Try to extract JSON from response
		response = response.strip()

		# Handle markdown code blocks
		if response.startswith('```'):
			lines = response.split('\n')
			start_idx = 1 if lines[0].startswith('```') else 0
			end_idx = len(lines)
			for i, line in enumerate(lines[start_idx:], start_idx):
				if line.strip() == '```':
					end_idx = i
					break
			response = '\n'.join(lines[start_idx:end_idx])

		# Validate JSON
		try:
			parsed = json.loads(response)
			return json.dumps(parsed, indent=2, ensure_ascii=False)
		except json.JSONDecodeError:
			# If not valid JSON, return as-is but cleaned
			return response.strip()
