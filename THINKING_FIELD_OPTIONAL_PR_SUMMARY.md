# PR: Make Thinking Field Optional in Browser-Use Agent

This PR implements the feature to optionally deactivate the thinking field in the browser-use agent, providing a more concise output format when detailed reasoning is not needed.

## Overview

The browser-use agent previously required a `thinking` field in its structured output, which contained detailed reasoning about the agent's decision-making process. This PR adds the ability to disable this field, making the output more streamlined for use cases where detailed reasoning is not required.

## Changes Made

### 1. Added `use_thinking` Setting

**File:** `browser_use/agent/views.py`
- Added `use_thinking: bool = True` to `AgentSettings` class
- Made `thinking` field optional (`str | None = None`) in both `AgentBrain` and `AgentOutput` models
- Added `type_with_custom_actions_no_thinking()` static method for creating AgentOutput models without thinking field

### 2. Created Separate System Prompt for No-Thinking Mode

**File:** `browser_use/agent/system_prompt_no_thinking.md`
- New system prompt template that excludes all thinking-related instructions
- Focuses on evaluation, memory, next_goal, and actions only
- Provides clear and concise reasoning rules without requiring extensive thinking blocks

### 3. Updated System Prompt Loading Logic

**File:** `browser_use/agent/prompts.py`
- Modified `SystemPrompt` class to accept `use_thinking` parameter
- Added `_load_prompt_template()` method that automatically selects the appropriate template:
  - `system_prompt.md` when `use_thinking=True` (default)
  - `system_prompt_no_thinking.md` when `use_thinking=False`

### 4. Enhanced Agent Service

**File:** `browser_use/agent/service.py`
- Added `use_thinking` parameter to Agent constructor
- Updated `_setup_action_models()` to create appropriate output models based on thinking setting
- Updated `_update_action_models_for_page()` to handle both thinking and no-thinking models
- Modified `log_response()` function to handle cases where thinking might be None
- **RESTORED CRITICAL CODE**: Fixed accidentally removed new elements detection code in `multi_act()` method that prevents action sequence interruption when new elements appear

### 5. Updated Message Manager

**File:** `browser_use/agent/message_manager/service.py`
- Added `use_thinking` parameter to MessageManager constructor
- Modified `_init_messages()` to provide different examples based on thinking setting:
  - Full thinking examples when `use_thinking=True`
  - Concise examples without thinking field when `use_thinking=False`
- Updated `_update_agent_history_description()` to handle None thinking values
- Fixed redundant newlines in file content examples (e.g., `"# Github Repositories:\n"` instead of multiline strings)
- **FIXED TAGS**: Updated `add_new_task()` method to use `<system>` tags instead of `<s>` tags

### 6. Fixed System Message Tags

**Both system prompt files:**
- Changed `<s>` tags to `<system>` tags for consistency
- Changed `<o>` tags to `<output>` tags for better clarity
- Updated all references to use the new tag format

### 7. Updated Agent History Serialization

**File:** `browser_use/agent/views.py`
- Enhanced `AgentHistory.model_dump()` to conditionally include thinking field only when present
- Ensures clean serialization when thinking is disabled

## Usage

### Basic Usage with Thinking (Default)
```python
from browser_use import Agent
from browser_use.llm import ChatOpenAI

agent = Agent(
    task="Find information about browser automation",
    llm=ChatOpenAI(model="gpt-4o"),
    use_thinking=True  # Default behavior - includes thinking field
)
```

### Streamlined Usage without Thinking
```python
from browser_use import Agent
from browser_use.llm import ChatOpenAI

agent = Agent(
    task="Find information about browser automation", 
    llm=ChatOpenAI(model="gpt-4o"),
    use_thinking=False  # Disables thinking field for cleaner output
)
```

## Benefits

1. **Reduced Token Usage**: Eliminates thinking field when not needed, saving tokens and costs
2. **Faster Processing**: Less content to generate and process
3. **Cleaner Output**: More focused responses for production use cases
4. **Backward Compatibility**: Default behavior unchanged (`use_thinking=True`)
5. **Debugging Flexibility**: Can still enable thinking for development and debugging

## Testing

The implementation has been tested to ensure:
- ✅ Default behavior remains unchanged (`use_thinking=True`)
- ✅ New no-thinking mode works correctly (`use_thinking=False`)
- ✅ System prompts load correctly for both modes
- ✅ Examples are provided appropriately for each mode
- ✅ Agent output models are created correctly
- ✅ History serialization handles optional thinking field
- ✅ Message manager creates appropriate examples
- ✅ All tags are consistent (`<system>` and `<output>`)
- ✅ Critical multi-action functionality preserved

## Files Modified

1. `browser_use/agent/views.py` - Core models and settings
2. `browser_use/agent/system_prompt_no_thinking.md` - New system prompt (created)
3. `browser_use/agent/system_prompt.md` - Updated tags
4. `browser_use/agent/prompts.py` - System prompt loading logic
5. `browser_use/agent/service.py` - Agent initialization and model setup
6. `browser_use/agent/message_manager/service.py` - Message handling and examples

This PR provides a clean, optional way to disable the thinking field while maintaining full backward compatibility and preserving all existing functionality.