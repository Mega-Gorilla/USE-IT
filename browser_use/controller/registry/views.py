from typing import Callable, Dict, Type

from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict


class RegisteredAction(BaseModel):
	"""Model for a registered action"""

	name: str
	description: str
	function: Callable
	param_model: Type[BaseModel]

	# filters: provide specific domains or a function to determine whether the action should be available on the given page or not
	domains: list[str] | None = None  # e.g. ['*.google.com', 'www.bing.com', 'yahoo.*]
	page_filter: Callable[[Page], bool] | None = None

	model_config = ConfigDict(arbitrary_types_allowed=True)

	def prompt_description(self) -> str:
		"""Get a description of the action for the prompt"""
		skip_keys = ['title']
		s = f'{self.description}: \n'
		s += '{' + str(self.name) + ': '
		s += str(
			{
				k: {sub_k: sub_v for sub_k, sub_v in v.items() if sub_k not in skip_keys}
				for k, v in self.param_model.schema()['properties'].items()
			}
		)
		s += '}'
		return s


class ActionModel(BaseModel):
	"""Base model for dynamically created action models"""

	# this will have all the registered actions, e.g.
	# click_element = param_model = ClickElementParams
	# done = param_model = None
	#
	model_config = ConfigDict(arbitrary_types_allowed=True)

	def get_index(self) -> int | None:
		"""Get the index of the action"""
		# {'clicked_element': {'index':5}}
		params = self.model_dump(exclude_unset=True).values()
		if not params:
			return None
		for param in params:
			if param is not None and 'index' in param:
				return param['index']
		return None

	def set_index(self, index: int):
		"""Overwrite the index of the action"""
		# Get the action name and params
		action_data = self.model_dump(exclude_unset=True)
		action_name = next(iter(action_data.keys()))
		action_params = getattr(self, action_name)

		# Update the index directly on the model
		if hasattr(action_params, 'index'):
			action_params.index = index


class ActionRegistry(BaseModel):
	"""Model representing the action registry"""

	actions: Dict[str, RegisteredAction] = {}

	def _match_domain(self, domain_pattern: str, url: str) -> bool:
		"""
		Match a domain pattern against a URL using glob patterns.

		Args:
			domain_pattern: A domain pattern that can include glob patterns (* wildcard)
			url: The URL to match against

		Returns:
			True if the URL's domain matches the pattern, False otherwise
		"""
		import fnmatch
		from urllib.parse import urlparse

		# Parse the URL to get the domain
		try:
			parsed_url = urlparse(url)
			if not parsed_url.netloc:
				return False

			domain = parsed_url.netloc
			# Remove port if present
			if ':' in domain:
				domain = domain.split(':')[0]

			# Perform glob matching
			return fnmatch.fnmatch(domain, domain_pattern)
		except Exception:
			return False

	def get_prompt_description(self, page=None) -> str:
		"""Get a description of all actions for the prompt

		Args:
			page: If provided, filter actions by page using page_filter and domains.

		Returns:
			A string description of available actions.
			- If page is None: return only actions with no page_filter and no domains (for system prompt)
			- If page is provided: return filtered actions for the current page
		"""
		if page is None:
			# For system prompt (no page provided), include only actions with no filters
			return '\n'.join(
				action.prompt_description()
				for action in self.actions.values()
				if action.page_filter is None and action.domains is None
			)

		# For step-by-step prompts, include filtered actions for the current page
		filtered_actions = []
		for action in self.actions.values():
			# Check page_filter if present
			page_filter_match = True  # Default to True if no filter
			if action.page_filter is not None:
				page_filter_match = action.page_filter(page)

			# Check domains if present
			domains_match = False
			if action.domains is not None and page.url:
				# Try to match any of the domain patterns
				for domain_pattern in action.domains:
					if self._match_domain(domain_pattern, page.url):
						domains_match = True
						break

			# Include action if either filter matches
			if page_filter_match or domains_match:
				filtered_actions.append(action)

		return '\n'.join(action.prompt_description() for action in filtered_actions)
