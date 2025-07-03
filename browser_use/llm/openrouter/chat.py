from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar, overload

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from openai.types.chat.chat_completion import ChatCompletion
from pydantic import BaseModel

from browser_use.llm.base import BaseChatModel
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.openrouter.serializer import OpenRouterMessageSerializer
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage

T = TypeVar('T', bound=BaseModel)


@dataclass
class ChatOpenRouter(BaseChatModel):
    """
    A wrapper around OpenRouter's chat API, which provides access to various LLM models
    through a unified OpenAI-compatible interface.

    This class implements the BaseChatModel protocol for OpenRouter's API.
    """

    # Model configuration
    model: str

    # Model params
    temperature: float | None = None

    # Client initialization parameters
    api_key: str | None = None
    http_referer: str | None = None  # OpenRouter specific parameter for tracking
    base_url: str | httpx.URL = "https://openrouter.ai/api/v1"
    timeout: float | httpx.Timeout | None = None
    max_retries: int = 10
    default_headers: Mapping[str, str] | None = None
    default_query: Mapping[str, object] | None = None
    http_client: httpx.AsyncClient | None = None
    _strict_response_validation: bool = False

    # Static
    @property
    def provider(self) -> str:
        return 'openrouter'

    def _get_client_params(self) -> dict[str, Any]:
        """Prepare client parameters dictionary."""
        # Define base client params
        base_params = {
            'api_key': self.api_key,
            'base_url': self.base_url,
            'timeout': self.timeout,
            'max_retries': self.max_retries,
            'default_headers': self.default_headers,
            'default_query': self.default_query,
            '_strict_response_validation': self._strict_response_validation,
        }

        # Create client_params dict with non-None values
        client_params = {k: v for k, v in base_params.items() if v is not None}

        # Add http_client if provided
        if self.http_client is not None:
            client_params['http_client'] = self.http_client

        return client_params

    def get_client(self) -> AsyncOpenAI:
        """
        Returns an AsyncOpenAI client configured for OpenRouter.

        Returns:
            AsyncOpenAI: An instance of the AsyncOpenAI client with OpenRouter base URL.
        """
        client_params = self._get_client_params()
        return AsyncOpenAI(**client_params)

    @property
    def name(self) -> str:
        return str(self.model)

    def _get_usage(self, response: ChatCompletion) -> ChatInvokeUsage | None:
        """Extract usage information from the OpenRouter response."""
        usage = (
            ChatInvokeUsage(
                prompt_tokens=response.usage.prompt_tokens,
                prompt_cached_tokens=response.usage.prompt_tokens_details.cached_tokens
                if hasattr(response.usage, 'prompt_tokens_details') and response.usage.prompt_tokens_details is not None
                else None,
                prompt_cache_creation_tokens=None,
                prompt_image_tokens=None,
                # Completion
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )
            if response.usage is not None
            else None
        )
        return usage

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: None = None) -> ChatInvokeCompletion[str]:
        ...

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T]) -> ChatInvokeCompletion[T]:
        ...

    async def ainvoke(
            self, messages: list[BaseMessage], output_format: type[T] | None = None
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        """
        Invoke the model with the given messages through OpenRouter.

        Args:
            messages: List of chat messages
            output_format: Optional Pydantic model class for structured output

        Returns:
            Either a string response or an instance of output_format
        """
        openrouter_messages = OpenRouterMessageSerializer.serialize_messages(messages)

        # Set up extra headers for OpenRouter
        extra_headers = {}
        if self.http_referer:
            extra_headers["HTTP-Referer"] = self.http_referer

        try:
            if output_format is None:
                # Return string response
                response = await self.get_client().chat.completions.create(
                    model=self.model,
                    messages=openrouter_messages,
                    temperature=self.temperature,
                    extra_headers=extra_headers,
                )

                usage = self._get_usage(response)
                return ChatInvokeCompletion(
                    completion=response.choices[0].message.content or '',
                    usage=usage,
                )

            else:
                # Create a JSON schema for structured output
                schema = SchemaOptimizer.create_optimized_json_schema(output_format)

                # Create a more explicit instruction based on the schema fields
                schema_fields = list(schema.get("properties", {}).keys())
                fields_str = ", ".join(schema_fields)

                # Construct instructions about the required format
                json_instruction = (
                    f"Please provide your response as a JSON object with these fields: {fields_str}. "
                    f"For example: {{"
                )

                # Add example values for each field
                for field in schema_fields:
                    json_instruction += f'"{field}": "<{field}_value>", '

                # Remove trailing comma and space and close the example
                json_instruction = json_instruction.rstrip(", ") + "}."

                # Add the instructions to system or user message
                has_system = any(msg['role'] == 'system' for msg in openrouter_messages)

                if has_system:
                    # Append to the first system message
                    for msg in openrouter_messages:
                        if msg['role'] == 'system':
                            msg['content'] += f" {json_instruction}"
                            break
                else:
                    # If no system message, add a new system message with the instructions
                    system_message = {
                        'role': 'system',
                        'content': f"You are a helpful assistant. {json_instruction}"
                    }
                    openrouter_messages.insert(0, system_message)

                # Return structured response
                response = await self.get_client().chat.completions.create(
                    model=self.model,
                    messages=openrouter_messages,
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                    extra_headers=extra_headers,
                )

                if response.choices[0].message.content is None:
                    raise ModelProviderError(
                        message='Failed to parse structured output from model response',
                        status_code=500,
                        model=self.name,
                    )

                usage = self._get_usage(response)

                try:
                    # Try to parse the JSON response
                    parsed = output_format.model_validate_json(response.choices[0].message.content)
                    return ChatInvokeCompletion(
                        completion=parsed,
                        usage=usage,
                    )
                except Exception as e:
                    # If parsing fails, provide a more detailed error
                    error_msg = (
                        f"Failed to parse response as {output_format.__name__}: {str(e)}. "
                        f"Response content: {response.choices[0].message.content}"
                    )
                    raise ModelProviderError(
                        message=error_msg,
                        status_code=500,
                        model=self.name,
                    ) from e

        except RateLimitError as e:
            error_message = e.response.json().get('error', {})
            error_message = (
                error_message.get('message', 'Unknown model error') if isinstance(error_message,
                                                                                  dict) else error_message
            )
            raise ModelProviderError(
                message=error_message,
                status_code=e.response.status_code,
                model=self.name,
            ) from e

        except APIConnectionError as e:
            raise ModelProviderError(message=str(e), model=self.name) from e

        except APIStatusError as e:
            try:
                error_message = e.response.json().get('error', {})
            except Exception:
                error_message = e.response.text
            error_message = (
                error_message.get('message', 'Unknown model error') if isinstance(error_message,
                                                                                  dict) else error_message
            )
            raise ModelProviderError(
                message=error_message,
                status_code=e.response.status_code,
                model=self.name,
            ) from e

        except Exception as e:
            raise ModelProviderError(message=str(e), model=self.name) from e