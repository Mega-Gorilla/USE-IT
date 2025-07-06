# PR: Add `include_tool_call_examples` Parameter to Agent

## Summary
Added a new optional parameter `include_tool_call_examples` to the Agent class initialization that controls whether the tool call examples (3 messages after the system prompt) are included in the message history.

## Changes Made

### 1. Added parameter to Agent class (`browser_use/agent/service.py`)
- Added `include_tool_call_examples: bool = True` parameter to the `Agent.__init__()` method
- Parameter defaults to `True` to maintain backward compatibility

### 2. Updated AgentSettings class (`browser_use/agent/views.py`)
- Added `include_tool_call_examples: bool = True` field to the `AgentSettings` model

### 3. Updated MessageManager class (`browser_use/agent/message_manager/service.py`)
- Added `include_tool_call_examples: bool = True` parameter to the `MessageManager.__init__()` method
- Stored the parameter as an instance variable `self.include_tool_call_examples`
- Modified `_init_messages()` method to conditionally include the 3 example messages based on this parameter

### 4. Updated Agent-MessageManager integration (`browser_use/agent/service.py`)
- Passed `include_tool_call_examples=self.settings.include_tool_call_examples` to the MessageManager constructor

## Usage

### Default behavior (backward compatible)
```python
# Tool call examples are included by default
agent = Agent(
    task="Your task",
    llm=your_llm_model
)
```

### Disable tool call examples
```python
# Tool call examples are excluded
agent = Agent(
    task="Your task", 
    llm=your_llm_model,
    include_tool_call_examples=False
)
```

## Technical Details

The tool call examples consist of 3 messages that are normally added after the system prompt:

1. **UserMessage**: Example introduction message
2. **AssistantMessage**: Example response with thinking and actions
3. **UserMessage**: Example result message

When `include_tool_call_examples=False`, these messages are skipped, resulting in:
- **With examples**: 4 messages (1 system + 3 example messages)
- **Without examples**: 1 message (system only)

This allows for:
- Cleaner message history for users who don't need examples
- Reduced token usage when examples are not needed
- More flexibility in prompt engineering

## Backward Compatibility
- Default value is `True`, so existing code continues to work unchanged
- No breaking changes to existing functionality