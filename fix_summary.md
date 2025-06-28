# ExtendedOutputModel Schema Fix Summary

## Issue
The user encountered an error: `ExtendedOutputModel is not fully defined; you should define Optional, then call ExtendedOutputModel.model_rebuild()` when using custom output schemas in the eval/service.py.

The error occurred when passing a JSON schema like:
```json
{
  "type": "object", 
  "properties": {
    "website": { "type": "string" },
    "part": { "type": "string" },
    "part_url": { "type": [ "string", "null" ] },
    "price": { "type": [ "number", "null" ] },
    "error": { "type": [ "string", "null" ] }
  },
  "required": [ "website", "part" ]
}
```

## Root Cause
In `browser_use/controller/service.py`, the `ExtendedOutputModel` class was dynamically created with a forward reference annotation (`data: output_model`) that Pydantic v2 couldn't resolve properly without rebuilding the model.

## Solution
Added `ExtendedOutputModel.model_rebuild()` after the dynamic model creation in the Controller's `__init__` method.

### Code Change
**File:** `browser_use/controller/service.py`

```python
# Before (lines 81-83):
class ExtendedOutputModel(BaseModel):  # type: ignore
    success: bool = True
    data: output_model  # type: ignore

# After (lines 81-86):
class ExtendedOutputModel(BaseModel):  # type: ignore
    success: bool = True
    data: output_model  # type: ignore

# Rebuild the model to resolve any forward references
ExtendedOutputModel.model_rebuild()
```

## Impact
- ✅ Fixes the schema validation error when using custom output models
- ✅ Preserves existing functionality for regular (non-structured) outputs
- ✅ No breaking changes to the API
- ✅ Validated with test schema matching user's use case

## UI Issue
The user also mentioned the UI shows "No detailed final response recorded". This is likely a separate frontend issue unrelated to the schema fix, as the final results should still flow through `history.final_result()` which returns the `extracted_content` from the last action result. The schema fix ensures the JSON output is properly generated and stored.