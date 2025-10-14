from __future__ import annotations

import hashlib
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from browser_use.agent.message_manager.utils import save_conversation
from browser_use.llm.messages import BaseMessage, ContentPartTextParam, UserMessage
from browser_use.observability import observe_debug
from browser_use.utils import URL_PATTERN, time_execution_async

if TYPE_CHECKING:
	from browser_use.agent.service import Agent
	from browser_use.agent.views import AgentOutput
	from browser_use.browser.views import BrowserStateSummary


class LLMHandler:
	"""Coordinate LLM interactions, retries, and response post-processing."""

	def __init__(self, agent: 'Agent') -> None:
		self.agent = agent

	async def get_model_output_with_retry(self, input_messages: list[BaseMessage]) -> 'AgentOutput':
		"""Call the LLM and retry once if no actions are produced."""
		model_output = await self.get_model_output(input_messages)
		self.agent.logger.debug(
			f'✅ Step {self.agent.state.n_steps}: Got LLM response with {len(model_output.action) if model_output.action else 0} actions'
		)

		if (
			not model_output.action
			or not isinstance(model_output.action, list)
			or all(action.model_dump() == {} for action in model_output.action)
		):
			self.agent.logger.warning('Model returned empty action. Retrying...')

			clarification_message = UserMessage(
				content='You forgot to return an action. Please respond with a valid JSON action according to the expected schema with your assessment and next actions.'
			)

			retry_messages = input_messages + [clarification_message]
			model_output = await self.get_model_output(retry_messages)

			if not model_output.action or all(action.model_dump() == {} for action in model_output.action):
				self.agent.logger.warning('Model still returned empty after retry. Inserting safe noop action.')
				action_instance = self.agent.ActionModel()
				setattr(
					action_instance,
					'done',
					{
						'success': False,
						'text': 'No next action returned by LLM!',
					},
				)
				model_output.action = [action_instance]

		return model_output

	@time_execution_async('--get_next_action')
	@observe_debug(ignore_input=True, ignore_output=True, name='get_model_output')
	async def get_model_output(self, input_messages: list[BaseMessage]) -> 'AgentOutput':
		"""Invoke the LLM and return the parsed output."""
		urls_replaced = self._process_messages_and_shorten_urls(input_messages)

		kwargs: dict = {'output_format': self.agent.AgentOutput}

		try:
			response = await self.agent.llm.ainvoke(input_messages, **kwargs)
			parsed: AgentOutput = response.completion  # type: ignore[assignment]

			if urls_replaced:
				self._restore_urls_in_model(parsed, urls_replaced)

			if len(parsed.action) > self.agent.settings.max_actions_per_step:
				parsed.action = parsed.action[: self.agent.settings.max_actions_per_step]

			if not (hasattr(self.agent.state, 'paused') and (self.agent.state.paused or self.agent.state.stopped)):
				self.agent.telemetry_handler.log_model_response(parsed)

			self.agent.telemetry_handler.log_next_action_summary(parsed)
			return parsed
		except ValidationError:
			raise

	async def handle_post_llm_processing(
		self,
		browser_state_summary: 'BrowserStateSummary',
		input_messages: list[BaseMessage],
	) -> None:
		"""Run callbacks and persist conversations after LLM responses."""
		agent = self.agent
		if agent.register_new_step_callback and agent.state.last_model_output:
			if inspect.iscoroutinefunction(agent.register_new_step_callback):
				await agent.register_new_step_callback(
					browser_state_summary,
					agent.state.last_model_output,
					agent.state.n_steps,
				)
			else:
				agent.register_new_step_callback(
					browser_state_summary,
					agent.state.last_model_output,
					agent.state.n_steps,
				)

		if agent.settings.save_conversation_path and agent.state.last_model_output:
			conversation_dir = Path(agent.settings.save_conversation_path)
			conversation_filename = f'conversation_{agent.id}_{agent.state.n_steps}.txt'
			target = conversation_dir / conversation_filename
			await save_conversation(
				input_messages,
				agent.state.last_model_output,
				target,
				agent.settings.save_conversation_path_encoding,
			)

	def _process_messages_and_shorten_urls(self, input_messages: list[BaseMessage]) -> dict[str, str]:
		"""Shorten long URLs inside message payloads."""
		from browser_use.llm.messages import AssistantMessage

		urls_replaced: dict[str, str] = {}

		for message in input_messages:
			if isinstance(message, (UserMessage, AssistantMessage)):
				if isinstance(message.content, str):
					message.content, replaced = self._replace_urls_in_text(message.content)
					urls_replaced.update(replaced)
				elif isinstance(message.content, list):
					for part in message.content:
						if isinstance(part, ContentPartTextParam):
							part.text, replaced = self._replace_urls_in_text(part.text)
							urls_replaced.update(replaced)

		return urls_replaced

	def _replace_urls_in_text(self, text: str) -> tuple[str, dict[str, str]]:
		replaced_urls: dict[str, str] = {}

		def replace_url(match: re.Match) -> str:
			original_url = match.group(0)

			query_start = original_url.find('?')
			fragment_start = original_url.find('#')

			after_path_start = len(original_url)
			if query_start != -1:
				after_path_start = min(after_path_start, query_start)
			if fragment_start != -1:
				after_path_start = min(after_path_start, fragment_start)

			base_url = original_url[:after_path_start]
			after_path = original_url[after_path_start:]

			if len(after_path) <= self.agent._url_shortening_limit:
				return original_url

			if after_path:
				truncated_after_path = after_path[: self.agent._url_shortening_limit]
				hash_obj = hashlib.md5(after_path.encode('utf-8'))
				short_hash = hash_obj.hexdigest()[:7]
				shortened = f'{base_url}{truncated_after_path}...{short_hash}'
				if len(shortened) < len(original_url):
					replaced_urls[shortened] = original_url
					return shortened

			return original_url

		return URL_PATTERN.sub(replace_url, text), replaced_urls

	def _restore_urls_in_model(self, parsed: 'AgentOutput', url_replacements: dict[str, str]) -> None:
		self._recursive_process_model(parsed, url_replacements)

	def _recursive_process_model(self, model: BaseModel, url_replacements: dict[str, str]) -> None:
		for field_name, field_value in model.__dict__.items():
			if isinstance(field_value, str):
				setattr(model, field_name, self._replace_shortened_urls_in_string(field_value, url_replacements))
			elif isinstance(field_value, BaseModel):
				self._recursive_process_model(field_value, url_replacements)
			elif isinstance(field_value, dict):
				self._recursive_process_dict(field_value, url_replacements)
			elif isinstance(field_value, (list, tuple)):
				setattr(model, field_name, self._recursive_process_iterable(field_value, url_replacements))

	def _recursive_process_dict(self, dictionary: dict, url_replacements: dict[str, str]) -> None:
		for key, value in dictionary.items():
			if isinstance(value, str):
				dictionary[key] = self._replace_shortened_urls_in_string(value, url_replacements)
			elif isinstance(value, BaseModel):
				self._recursive_process_model(value, url_replacements)
			elif isinstance(value, dict):
				self._recursive_process_dict(value, url_replacements)
			elif isinstance(value, (list, tuple)):
				dictionary[key] = self._recursive_process_iterable(value, url_replacements)

	def _recursive_process_iterable(self, container: list | tuple, url_replacements: dict[str, str]) -> list | tuple:
		if isinstance(container, tuple):
			processed_items = []
			for item in container:
				if isinstance(item, str):
					processed_items.append(self._replace_shortened_urls_in_string(item, url_replacements))
				elif isinstance(item, BaseModel):
					self._recursive_process_model(item, url_replacements)
					processed_items.append(item)
				elif isinstance(item, dict):
					self._recursive_process_dict(item, url_replacements)
					processed_items.append(item)
				elif isinstance(item, (list, tuple)):
					processed_items.append(self._recursive_process_iterable(item, url_replacements))
				else:
					processed_items.append(item)
			return tuple(processed_items)

		for i, item in enumerate(container):
			if isinstance(item, str):
				container[i] = self._replace_shortened_urls_in_string(item, url_replacements)
			elif isinstance(item, BaseModel):
				self._recursive_process_model(item, url_replacements)
			elif isinstance(item, dict):
				self._recursive_process_dict(item, url_replacements)
			elif isinstance(item, (list, tuple)):
				container[i] = self._recursive_process_iterable(item, url_replacements)
		return container

	def _replace_shortened_urls_in_string(self, text: str, url_replacements: dict[str, str]) -> str:
		result = text
		for shortened_url, original_url in url_replacements.items():
			result = result.replace(shortened_url, original_url)
		return result
