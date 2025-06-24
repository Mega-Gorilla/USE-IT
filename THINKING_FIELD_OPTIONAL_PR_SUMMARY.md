# PR: Make Thinking Field Optional in Browser-Use Agent

This PR implements the feature to optionally deactivate the thinking field in the browser-use agent, providing a more concise output format when detailed reasoning is not needed.

## Overview

The browser-use agent previously required a `thinking` field in its structured output, which contained detailed reasoning about the agent's decision-making process. This PR adds the ability to disable this field, making the output more streamlined for use cases where detailed reasoning is not required.

## Changes Made

### 1. Added `use_thinking` Setting

**File:** `browser_use/agent/views.py`
- Added `use_thinking: bool = True` to `AgentSettings` class
- Made `thinking` field optional (`str | None = None`) in both `AgentBrain` and `AgentOutput` models

### 2. Created No-Thinking System Prompt Template

**File:** `browser_use/agent/system_prompt_no_thinking.md`
- New system prompt template that excludes all references to thinking
- Simplified reasoning rules section
- Updated output format specification to exclude thinking field
- Contains all other functionality (browser rules, file system, task completion, etc.)

### 3. Updated System Prompt Loading Logic

**File:** `browser_use/agent/prompts.py`
- Added `use_thinking: bool = True` parameter to `SystemPrompt.__init__()`
- Modified `_load_prompt_template()` to load the appropriate template:
  - `system_prompt.md` when `use_thinking=True` (default)
  - `system_prompt_no_thinking.md` when `use_thinking=False`

### 4. Enhanced Agent Constructor

**File:** `browser_use/agent/service.py`
- Added `use_thinking: bool = True` parameter to `Agent.__init__()`
- Pass `use_thinking` parameter to `AgentSettings`
- Pass `use_thinking` parameter to `SystemPrompt` initialization
- Pass `use_thinking` parameter to `MessageManager` initialization

### 5. Updated Action Model Generation

**File:** `browser_use/agent/views.py`
- Added `type_with_custom_actions_no_thinking()` static method to create AgentOutput models without thinking field
- This method creates dynamic models that exclude the thinking field from the schema

**File:** `browser_use/agent/service.py`
- Updated `_setup_action_models()` and `_update_action_models_for_page()` to use appropriate model based on `use_thinking` setting

### 6. Enhanced Message Manager

**File:** `browser_use/agent/message_manager/service.py`
- Added `use_thinking: bool = True` parameter to `MessageManager.__init__()`
- Updated `_init_messages()` to provide different examples:
  - Examples with thinking field when `use_thinking=True`
  - Examples without thinking field when `use_thinking=False`

### 7. Updated Logging and Serialization

**File:** `browser_use/agent/service.py`
- Modified `log_response()` function to only log thinking when present
- Updated `AgentHistory.model_dump()` to conditionally include thinking field

## Usage Examples

### With Thinking (Default Behavior)
```python
from browser_use import Agent
from browser_use.llm import ChatOpenAI

agent = Agent(
    task="Navigate to GitHub and find trending repositories",
    llm=ChatOpenAI(model="gpt-4o"),
    use_thinking=True  # or omit (default)
)
```

Output includes detailed reasoning:
```json
{
  "thinking": "I need to navigate to GitHub and look for trending repositories...",
  "evaluation_previous_goal": "Successfully loaded GitHub homepage",
  "memory": "On GitHub homepage, ready to find trending repos",
  "next_goal": "Click on 'Explore' to find trending repositories",
  "action": [{"click_element_by_index": {"index": 4}}]
}
```

### Without Thinking (New Feature)
```python
from browser_use import Agent
from browser_use.llm import ChatOpenAI

agent = Agent(
    task="Navigate to GitHub and find trending repositories",
    llm=ChatOpenAI(model="gpt-4o"),
    use_thinking=False
)
```

Output is more concise:
```json
{
  "evaluation_previous_goal": "Successfully loaded GitHub homepage",
  "memory": "On GitHub homepage, ready to find trending repos",
  "next_goal": "Click on 'Explore' to find trending repositories",
  "action": [{"click_element_by_index": {"index": 4}}]
}
```

## Benefits

1. **Token Efficiency**: Reduces token usage when detailed reasoning is not needed
2. **Faster Processing**: Smaller output means faster model responses
3. **Cleaner Output**: More focused output for production use cases
4. **Backward Compatibility**: Default behavior remains unchanged
5. **Flexible Configuration**: Easy to toggle based on requirements

## Testing

The implementation maintains backward compatibility:
- Default behavior (`use_thinking=True`) works exactly as before
- All existing tests should pass without modification
- New functionality can be tested by setting `use_thinking=False`

## Technical Details

The implementation uses:
- Dynamic Pydantic model creation for structured output schemas
- Template-based system prompt selection
- Conditional field inclusion in serialization
- Type-safe parameter passing throughout the chain

All changes maintain the existing API while adding the new optional functionality.