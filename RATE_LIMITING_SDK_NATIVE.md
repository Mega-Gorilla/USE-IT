# SDK-Native Rate Limiting Implementation

## Overview

This implementation leverages the built-in retry mechanisms provided by each LLM provider's SDK, significantly simplifying our retry logic while providing more robust error handling.

## Provider-Specific Implementations

### OpenAI (`browser_use/llm/openai/chat.py`)
- **Built-in Support**: ✅ Full SDK retry support
- **Configuration**: `max_retries=10` (increased from default 2)
- **Features**: 
  - Exponential backoff with jitter
  - Retry-After header support
  - Automatic rate limit detection (429 errors)
  - Server error retries (5xx errors)
  - Connection error handling

### Anthropic (`browser_use/llm/anthropic/chat.py`)
- **Built-in Support**: ✅ Full SDK retry support  
- **Configuration**: `max_retries=10` (increased from default 2)
- **Features**:
  - Intelligent error classification (RateLimitError, APIStatusError)
  - Exponential backoff
  - Server overload handling (529 errors)

### Groq (`browser_use/llm/groq/chat.py`)
- **Built-in Support**: ✅ Full SDK retry support
- **Configuration**: `max_retries=10` (increased from DEFAULT_MAX_RETRIES)
- **Features**:
  - Rate limit handling (429 errors)
  - Capacity exceeded handling (498 errors)
  - Automatic exponential backoff

### Google/Gemini (`browser_use/llm/google/chat.py`)
- **Built-in Support**: ❌ Limited SDK retry support
- **Configuration**: Custom manual retry with 10 attempts
- **Features**:
  - Pattern-based error detection
  - Manual exponential backoff (1s → 60s max)
  - Retry on: rate limits, server errors, connection issues
  - Skip retry on: authentication, bad requests

### Azure OpenAI (`browser_use/llm/azure/chat.py`)
- **Built-in Support**: ✅ Inherits from ChatOpenAI
- **Configuration**: `max_retries=10` (inherited)
- **Features**: Same as OpenAI (inherits all retry logic)

## Benefits of SDK-Native Approach

1. **Reliability**: Provider SDKs know best how to handle their own error patterns
2. **Maintenance**: Reduces custom retry code we need to maintain
3. **Performance**: SDKs often implement optimizations like jitter and Retry-After headers
4. **Accuracy**: Provider-specific error classification is more accurate
5. **Future-Proof**: Automatically benefits from SDK improvements

## Error Handling Strategy

### Retryable Errors
- Rate limits (429, Resource Exhausted)
- Server errors (5xx)  
- Connection timeouts/network issues
- Provider-specific capacity errors (e.g., Groq 498)

### Non-Retryable Errors  
- Authentication errors (401, 403)
- Bad requests (400, 422)
- Not found (404)
- Quota/billing issues (payment required)

## Configuration

All providers now default to **10 retries** instead of 2-3, making browser-use more resilient to temporary API issues during long-running automation tasks.

Users can still override this by setting `max_retries` when instantiating models:

```python
# Use fewer retries for testing
model = ChatOpenAI(model="gpt-4o", max_retries=3)

# Use more retries for production
model = ChatOpenAI(model="gpt-4o", max_retries=15)
``` 
