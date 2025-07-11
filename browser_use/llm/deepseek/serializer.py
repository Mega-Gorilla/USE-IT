"""
DeepSeekMessageSerializer
~~~~~~~~~~~~~~~~~~~~~~~~~
把 browser-use 自定义消息格式序列化为 DeepSeek(OpenAI-兼容) /chat/completions
API 可接受的 messages 列表。
"""
from __future__ import annotations

import base64
import json
from typing import Any, overload

from browser_use.llm.messages import (  # 同 Anthropic/Ollama 依赖
    AssistantMessage,
    BaseMessage,
    ContentPartImageParam,
    ContentPartTextParam,
    SystemMessage,
    ToolCall,
    UserMessage,
)

# OpenAI-兼容接口的 message 类型 = dict
MessageDict = dict[str, Any]


class DeepSeekMessageSerializer:
    """Serializer for converting browser-use messages to DeepSeek messages."""

    # -------- content 处理 --------------------------------------------------

    @staticmethod
    def _serialize_text_part(part: ContentPartTextParam) -> str:
        return part.text

    @staticmethod
    def _serialize_image_part(part: ContentPartImageParam) -> dict[str, Any]:
        url = part.image_url.url
        if url.startswith("data:"):
            # 直接透传 base64 dataURL
            return {"type": "image_url", "image_url": {"url": url}}
        # 远程 URL
        return {"type": "image_url", "image_url": {"url": url}}

    @staticmethod
    def _serialize_content(content: Any) -> str | list[dict[str, Any]]:
        """
        将 browser_use 消息 content 转成 OpenAI 兼容格式：
        - 纯文本 → str
        - 混合(文本/图片等) → list[{type:..., ...}, ...]
        """
        if content is None:
            return ""

        if isinstance(content, str):
            return content

        # content 为 list[ContentPart*]
        serialized: list[dict[str, Any]] = []
        for part in content:
            if part.type == "text":
                serialized.append(
                    {"type": "text", "text": DeepSeekMessageSerializer._serialize_text_part(part)}
                )
            elif part.type == "image_url":
                serialized.append(DeepSeekMessageSerializer._serialize_image_part(part))
            elif part.type == "refusal":
                serialized.append({"type": "text", "text": f"[Refusal] {part.refusal}"})
        return serialized

    # -------- Tool-call 处理 -------------------------------------------------

    @staticmethod
    def _serialize_tool_calls(tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        deepseek_tool_calls: list[dict[str, Any]] = []
        for tc in tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                arguments = {"arguments": tc.function.arguments}
            deepseek_tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": arguments,
                    },
                }
            )
        return deepseek_tool_calls

    # -------- 单条消息序列化 -------------------------------------------------

    @overload
    @staticmethod
    def serialize(message: UserMessage) -> MessageDict: ...

    @overload
    @staticmethod
    def serialize(message: SystemMessage) -> MessageDict: ...

    @overload
    @staticmethod
    def serialize(message: AssistantMessage) -> MessageDict: ...

    @staticmethod
    def serialize(message: BaseMessage) -> MessageDict:
        if isinstance(message, UserMessage):
            return {
                "role": "user",
                "content": DeepSeekMessageSerializer._serialize_content(message.content),
            }

        if isinstance(message, SystemMessage):
            return {
                "role": "system",
                "content": DeepSeekMessageSerializer._serialize_content(message.content),
            }

        if isinstance(message, AssistantMessage):
            msg: MessageDict = {
                "role": "assistant",
                "content": DeepSeekMessageSerializer._serialize_content(message.content),
            }
            if message.tool_calls:
                msg["tool_calls"] = DeepSeekMessageSerializer._serialize_tool_calls(
                    message.tool_calls
                )
            return msg

        raise ValueError(f"Unknown message type: {type(message)}")

    # -------- 列表序列化 -----------------------------------------------------

    @staticmethod
    def serialize_messages(messages: list[BaseMessage]) -> list[MessageDict]:
        return [DeepSeekMessageSerializer.serialize(m) for m in messages]
