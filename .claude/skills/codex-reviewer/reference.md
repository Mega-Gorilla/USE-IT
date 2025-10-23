# Codex Reviewer Reference

## Codex Configuration

### Model Selection

The review scripts use the default Codex model automatically. No model selection is required.

```bash
# Default model is used (recommended)
codex exec "review prompt"
```

**Note**: The `-m` flag exists but specific model selection is not necessary for reviews. The default model provides excellent results.

### Sandbox Configuration

The review scripts use `--full-auto` which enables `workspace-write` sandbox mode:

```bash
# Used in review scripts
codex exec --full-auto "review prompt"

# Equivalent to:
codex exec --sandbox workspace-write -a on-failure "review prompt"
```

**Why `workspace-write`?**

This setting allows Codex to:
- ✅ Create test scripts to verify issues/PRs
- ✅ Generate sample code for review suggestions
- ✅ Write temporary files for analysis
- ✅ Execute commands for deeper code inspection

**Safety guarantees**:
- ✅ Limited to workspace directory only
- ✅ Cannot write outside the project
- ✅ Requires approval only on command failure
- ✅ Balanced security and functionality

**Available sandbox modes**:
- `read-only`: Read-only access (most restrictive)
- `workspace-write`: Write within workspace (balanced) ← **Used**
- `danger-full-access`: Full system access (not recommended)

### Approval Policy

Configure when Codex asks for approval:

```bash
# Never ask (full auto) - recommended for reviews
codex exec --full-auto "review prompt"

# Ask on failure
codex exec -a on-failure "review prompt"

# Ask for untrusted commands
codex exec -a untrusted "review prompt"

# Never ask (bypass all approvals)
codex exec -a never "review prompt"
```


## Token Management

### Limiting Context Size

To avoid token limit errors:

```bash
# Limit body to 5000 characters
BODY=$(echo "${BODY}" | head -c 5000)

# Limit comments to first 10
COMMENTS=$(echo "${COMMENTS_JSON}" | jq -r '.comments[0:10]')

# Limit diff to 100 lines
DIFF=$(gh pr diff ${PR_NUMBER} --repo ${REPO} | head -100)

# Truncate long strings
TRUNCATED="${LONG_TEXT:0:10000}..."
```

### Checking Token Usage

Codex output includes token usage at the end:

```
tokens used
1,359
```

Monitor this to optimize context size.

### Token Cost Estimation

Rough estimates (varies by model):
- Small issue (~500 tokens): ~$0.01
- Medium issue (~2000 tokens): ~$0.04
- Large PR (~5000 tokens): ~$0.10

## Output Parsing

### Extracting Review from Codex Output

Codex exec output format:
```
OpenAI Codex v0.46.0
--------
[metadata]
--------
user
<prompt>

thinking
<thinking process>

codex
<actual review content>

tokens used
<number>
```

Extract review content:
```bash
# Method 1: sed between markers
REVIEW=$(echo "${OUTPUT}" | sed -n '/^codex$/,/^tokens used$/p' | sed '1d;$d')

# Method 2: grep and awk
REVIEW=$(echo "${OUTPUT}" | awk '/^codex$/,/^tokens used$/' | sed '1d;$d')
```

## Error Handling

### Common Errors and Solutions

**Error: "stdout is not a terminal"**
- Cause: Using interactive command in non-interactive mode
- Solution: Use `codex exec` instead of `codex` or `codex resume`

**Error: "authentication required"**
- Cause: Codex not authenticated
- Solution: Run `codex login`

**Error: Empty review output**
- Causes:
  - Token limit exceeded
  - Malformed prompt
  - API rate limiting
- Solutions:
  - Reduce context size
  - Check prompt formatting
  - Wait and retry

**Error: "Codex review timed out after 20 minutes"**
- Cause: Review processing exceeded 20-minute timeout
- Solution:
  - Try a smaller issue/PR
  - Reduce context size (fewer comments, shorter diff)
  - Split large reviews into multiple smaller ones

**Error: GitHub API rate limit**
- Cause: Too many GitHub API calls
- Solution: Wait 1 hour or use authenticated token with higher limits

## Progress Monitoring

### Real-time Status Updates

The review scripts provide progress updates every minute:

```
🤖 Requesting Codex review...
⏳ This may take several minutes (max 20 min timeout)...
⏱️  Processing... 1 minute(s) elapsed
⏱️  Processing... 2 minute(s) elapsed
⏱️  Processing... 3 minute(s) elapsed
📊 Token usage: 87819
✍️ Posting review to GitHub...
✅ Review posted successfully!
```

### Timeout Configuration

**Default timeout**: 20 minutes (1200 seconds)

To customize the timeout, edit the script:
```bash
# In review_issue.sh or review_pr.sh
MAX_TIME=1200  # Change to desired seconds
```

**Recommended timeouts**:
- Small issues (< 10 comments): 5 minutes (300s)
- Medium issues/PRs: 10 minutes (600s)
- Large issues/PRs: 20 minutes (1200s, default)


## Best Practices

1. **Keep Context Focused**: Include only relevant information
2. **Clear Prompts**: Specify what aspects to review
3. **Token Budget**: Monitor and optimize token usage
4. **Human Oversight**: Always review AI suggestions
5. **Iterate**: Refine prompts based on review quality
6. **Document**: Save successful prompts for reuse
7. **Rate Limits**: Space out reviews to avoid hitting limits
8. **Error Handling**: Always check for empty or malformed output
