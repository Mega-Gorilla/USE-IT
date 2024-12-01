from datetime import datetime
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from browser_use.agent.views import ActionResult
from browser_use.browser.views import BrowserState


class SystemPrompt:
	def __init__(self, action_description: str, current_date: datetime):
		self.default_action_description = action_description
		self.current_date = current_date

	def example_response(self) -> str:
		"""
		Returns an example response for the agent.

		Returns:
		    str: Example response
		"""
		return """{"current_state": {"valuation_previous_goal": "Success", "memory": "We applied already for 3/7 jobs, 1. ..., 2. ..., 3. ...", "next_goal": "Click on the button x to apply for the next job"}, "action": {"click_element": {"index": 44,"num_clicks": 2}}}"""

	def important_rules(self) -> str:
		"""
		Returns the important rules for the agent.

		Returns:
		    str: Important rules
		"""
		return """
1. Only use indexes that exist in the input list for click or input text actions. If no indexes exist, try alternative actions, e.g. go back, search google etc.
2. If stuck, try alternative approaches, e.g. go back, search google, or extract_page_content
3. When you are done with the complete task, use the done action. Make sure to have all information the user needs and return the result.
4. If an image is provided, use it to understand the context, the bounding boxes around the buttons have the same indexes as the interactive elements.
6. ALWAYS respond in the RESPONSE FORMAT with valid JSON.
7. If the page is empty use actions like "go_to_url", "search_google" or "open_tab"
8. Remember: Choose EXACTLY ONE action per response. Invalid combinations or multiple actions will be rejected.
9. If popups like cookies appear, accept or close them
10. Call 'done' when you are done with the task - dont hallucinate or make up actions which the user did not ask for
	"""

	def input_format(self) -> str:
		return """
Example:
33[:]\t<button>Interactive element</button>
_[:] Text content...

Explanation:
index[:] Interactible element with index. You can only interact with all elements which are clickable and refer to them by their index.
_[:] elements are just for more context, but not interactable.
\t: Tab indent (1 tab for depth 1 etc.). This is to help you understand which elements belong to each other.
"""

	def get_system_message(self) -> SystemMessage:
		"""
		Get the system prompt for the agent.

		Returns:
		    str: Formatted system prompt
		"""
		time_str = self.current_date.strftime('%Y-%m-%d %H:%M')

		AGENT_PROMPT = f"""
You are an AI agent that helps users interact with websites. You receive a list of interactive elements from the current webpage and must respond with specific actions. Today's date is {time_str}.

INPUT FORMAT:
{self.input_format()}

Your AVAILABLE ACTIONS:
{self.default_action_description}

Example:
{self.example_response()}

IMPORTANT RULES:
{self.important_rules()}
"""
		return SystemMessage(content=AGENT_PROMPT)


class AgentMessagePrompt:
	def __init__(
		self,
		state: BrowserState,
		result: Optional[ActionResult] = None,
		include_attributes: list[str] = [],
		max_error_length: int = 400,
	):
		self.state = state
		self.result = result
		self.max_error_length = max_error_length
		self.include_attributes = include_attributes

	def get_user_message(self) -> HumanMessage:
		state_description = f"""
Current url: {self.state.url}
Available tabs:
{self.state.tabs}
Interactive elements:
{self.state.element_tree.clickable_elements_to_string(include_attributes=self.include_attributes)}
        """

		if self.result:
			if self.result.extracted_content:
				state_description += f'\nResult of last action: {self.result.extracted_content}'
			if self.result.error:
				# only use last 300 characters of error
				error = self.result.error[-self.max_error_length :]
				state_description += f'\nError of last action: ...{error}'

		if self.state.screenshot:
			# Format message for vision model
			return HumanMessage(
				content=[
					{'type': 'text', 'text': state_description},
					{
						'type': 'image_url',
						'image_url': {'url': f'data:image/png;base64,{self.state.screenshot}'},
					},
				]
			)

		return HumanMessage(content=state_description)
