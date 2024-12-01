from datetime import datetime
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from browser_use.agent.views import ActionResult
from browser_use.browser.views import BrowserState


class SystemPrompt:
	def __init__(self, action_description: str, current_date: datetime):
		self.default_action_description = action_description
		self.current_date = current_date

	def important_rules(self) -> str:
		"""
		Returns the important rules for the agent.
		"""
		return """
1. RESPONSE FORMAT: You must ALWAYS respond with valid JSON in this exact format:
   {
     "current_state": {
       "evaluation_previous_goal": "Success|Failed|Unknown - Brief description of why",
       "memory": "Brief description of what has been done-What you need to remember until the end of the task",
       "next_goal": "What needs to be done with the next actions"
     },
     "action": [
       {
         "action_name": {
           // action-specific parameters
         }
       },
       // ... more actions in sequence
     ]
   }

2. ACTIONS: You can specify multiple actions to be executed in sequence. 

   Common action sequences:
   - Form filling: [
       {"input_text": {"index": 1, "text": "username"}},
       {"input_text": {"index": 2, "text": "password"}},
       {"click_element": {"index": 3}}
     ]
   - Navigation and extraction: [
       {"go_to_url": {"url": "https://example.com"}},
       {"extract_page_content": {}}
     ]

3. ELEMENT INTERACTION:
   - Only use indexes that exist in the provided element list
   - Each element has a unique index number (e.g., "33[:]<button>")
   - Elements marked with "_[:]" are non-interactive (for context only)

4. NAVIGATION & ERROR HANDLING:
   - If no suitable elements exist, use other functions to complete the task
   - If stuck, try alternative approaches
   - Handle popups/cookies by accepting or closing them

5. TASK COMPLETION:
   - Use the done action as the last action as soon as the task is complete
   - Don't hallucinate actions
   - If the user asks for specific information - make sure to include everything in the done function. This is what the user will see.

6. VISUAL CONTEXT:
   - When an image is provided, use it to understand the page layout
   - Bounding boxes with labels correspond to element indexes
   - Each bounding box and its label have the same color
   - Most often the label is on the left of the bounding box
   - Visual context helps verify element locations and relationships

7. ACTION SEQUENCING:
   - Actions are executed in the order they appear in the list 
   - Each action should logically follow from the previous one
   - If the page changes between actions, the sequence is interrupted and you get the new page state.
   - Only provide the action sequence until you think the DOM will change.
   - Try to be efficient. If the dom changes a little bit we find the right element.
"""

	def input_format(self) -> str:
		return """
INPUT STRUCTURE:
1. Current URL: The webpage you're currently on
2. Available Tabs: List of open browser tabs
3. Interactive Elements: List in the format:
   index[:]<element_type>element_text</element_type>
   - index: Numeric identifier for interaction
   - element_type: HTML element type (button, input, etc.)
   - element_text: Visible text or element description

Example:
33[:]<button>Submit Form</button>
_[:] Non-interactive text
    45[:]<input>Email field</input>     # Indented to show hierarchy

Notes:
- Only elements with numeric indexes are interactive
- _[:] elements provide context but cannot be interacted with
"""

	def get_system_message(self) -> SystemMessage:
		"""
		Get the system prompt for the agent.

		Returns:
		    str: Formatted system prompt
		"""
		time_str = self.current_date.strftime('%Y-%m-%d %H:%M')

		AGENT_PROMPT = f"""You are a precise browser automation agent that interacts with websites through structured commands. Your role is to:
1. Analyze the provided webpage elements and structure
2. Plan a sequence of actions to accomplish the given task
3. Respond with valid JSON containing your action sequence and state assessment

Current date and time: {time_str}

{self.input_format()}

{self.important_rules()}

Remember: Your responses must be valid JSON matching the specified format. Each action in the sequence must be valid and logically connected."""
		return SystemMessage(content=AGENT_PROMPT)


# Example:
# {self.example_response()}
# Your AVAILABLE ACTIONS:
# {self.default_action_description}


class AgentMessagePrompt:
	def __init__(
		self,
		state: BrowserState,
		result: Optional[List[ActionResult]] = None,
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
			for i, result in enumerate(self.result):
				if result.extracted_content:
					state_description += (
						f'\nResult of action {i + 1}/{len(self.result)}: {result.extracted_content}'
					)
				if result.error:
					# only use last 300 characters of error
					error = result.error[-self.max_error_length :]
					state_description += f'\nError of action {i + 1}/{len(self.result)}: ...{error}'

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
