# Codex Reviewer Examples

## Example 1: Review Issue Implementation Plan

**Scenario**: Issue #27 proposes GUI interactive mode implementation

```bash
scripts/review_issue.sh 27
```

**Expected Output**:
```
🔍 Fetching Issue #27 from Mega-Gorilla/USE-IT...
🤖 Requesting Codex review...
✍️ Posting review to GitHub...
✅ Review posted successfully!
View at: https://github.com/Mega-Gorilla/USE-IT/issues/27
```

**What Gets Reviewed**:
- Implementation feasibility of interactive approvals
- Threading concerns (asyncio ↔ Qt)
- Timeout handling approach
- UI/UX considerations
- Security implications
- Edge cases (dialog closed, timeouts)

**Expected Review Content**:
- Analysis of Future + Signal pattern
- Recommendations for thread safety
- Suggestions for timeout configuration
- UI improvement ideas

---

## Example 2: Review Pull Request

**Scenario**: PR #28 implements interactive approval feature

```bash
scripts/review_pr.sh 28
```

**Expected Output**:
```
🔍 Fetching PR #28 from Mega-Gorilla/USE-IT...
🤖 Requesting Codex review...
✍️ Posting review to GitHub...
✅ Review posted successfully!
View at: https://github.com/Mega-Gorilla/USE-IT/pull/28
```

**What Gets Reviewed**:
- Code quality and structure
- Thread safety implementation
- Error handling completeness
- Test coverage
- Documentation quality
- Potential race conditions

**Expected Review Content**:
- Code quality assessment
- Identified bugs or issues
- Performance considerations
- Security concerns
- Improvement suggestions

---

## Example 3: Security-Focused Review

**Scenario**: Review cloud/MCP deprecation plan for security

```bash
#!/bin/bash
ISSUE_NUMBER=25

# Fetch issue content
CONTEXT=$(gh issue view ${ISSUE_NUMBER} --repo Mega-Gorilla/USE-IT \
  --json title,body,comments --jq '{title, body, comments: [.comments[] | {author: .author.login, body}]}')

# Custom security review
PROMPT="SECURITY REVIEW - Focus on:
1. Data exposure during deprecation
2. Migration security
3. Credential handling during transition
4. Access control changes
5. Potential attack vectors

Issue Context:
${CONTEXT}"

REVIEW=$(codex exec --full-auto "${PROMPT}")

echo "${REVIEW}"
```

---

## Example 4: Performance-Focused Review

**Scenario**: Review a performance-critical PR

```bash
#!/bin/bash
PR_NUMBER=5

# Fetch PR details
PR_INFO=$(gh pr view ${PR_NUMBER} --repo Mega-Gorilla/USE-IT \
  --json title,body,commits,additions,deletions)

# Get diff
DIFF=$(gh pr diff ${PR_NUMBER} --repo Mega-Gorilla/USE-IT | head -200)

# Performance-focused review
PROMPT="PERFORMANCE REVIEW - Analyze:
1. Algorithm complexity (Big O)
2. Database query efficiency
3. Memory allocation patterns
4. Caching opportunities
5. API response time impact

PR Details:
${PR_INFO}

Code Changes:
${DIFF}"

REVIEW=$(codex exec --full-auto "${PROMPT}")

# Post review
gh pr comment ${PR_NUMBER} --repo Mega-Gorilla/USE-IT \
  --body "## 🚀 Performance Review

${REVIEW}

---
🤖 Automated performance analysis by OpenAI Codex"
```

---

## Example 5: Batch Review Multiple Issues

**Scenario**: Review all open issues with `enhancement` label

```bash
#!/bin/bash
set -euo pipefail

REPO="Mega-Gorilla/USE-IT"

# Get all open enhancement issues
ISSUES=$(gh issue list --repo ${REPO} --label enhancement --state open --json number --jq '.[].number')

for issue_num in ${ISSUES}; do
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Reviewing Issue #${issue_num}..."
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  scripts/review_issue.sh "${issue_num}"

  echo ""
  echo "⏳ Waiting 10 seconds (rate limiting)..."
  sleep 10
done

echo "✅ Batch review completed!"
```

---

## Example 6: Custom Review for API Design

**Scenario**: Review API endpoint design in an issue

```bash
#!/bin/bash
ISSUE_NUMBER=30  # Hypothetical API design issue

CONTEXT=$(gh issue view ${ISSUE_NUMBER} --repo Mega-Gorilla/USE-IT --json title,body)

PROMPT="API DESIGN REVIEW - Evaluate:

1. **Endpoint Naming**:
   - RESTful conventions
   - Consistency with existing API
   - Clarity and intuition

2. **Request/Response Schema**:
   - Data structure clarity
   - Required vs optional fields
   - Validation rules

3. **Error Handling**:
   - HTTP status codes
   - Error response format
   - Client-friendly messages

4. **Authentication**:
   - Auth method appropriateness
   - Token handling
   - Permission model

5. **Versioning**:
   - Version strategy
   - Backward compatibility
   - Deprecation plan

Issue Details:
${CONTEXT}"

codex exec --full-auto "${PROMPT}"
```

---

## Example 7: Database Schema Review

**Scenario**: Review database migration in PR

```bash
#!/bin/bash
PR_NUMBER=35  # Hypothetical migration PR

# Get migration files
FILES=$(gh pr view ${PR_NUMBER} --repo Mega-Gorilla/USE-IT \
  --json files --jq '.files[] | select(.path | contains("migration")) | .path')

if [ -z "${FILES}" ]; then
  echo "No migration files found"
  exit 0
fi

# Get diff of migration files
DIFF=""
for file in ${FILES}; do
  DIFF="${DIFF}\n\n=== ${file} ===\n"
  DIFF="${DIFF}$(gh pr diff ${PR_NUMBER} --repo Mega-Gorilla/USE-IT -- ${file})"
done

PROMPT="DATABASE MIGRATION REVIEW - Check:

1. **Schema Changes**:
   - Data type appropriateness
   - Constraints (NOT NULL, UNIQUE, etc.)
   - Default values

2. **Indexing**:
   - Index strategy
   - Query performance impact
   - Index maintenance cost

3. **Normalization**:
   - Normal form compliance
   - Redundancy
   - Join complexity

4. **Migration Safety**:
   - Rollback strategy
   - Data loss risks
   - Downtime requirements

5. **Performance**:
   - Large table migration
   - Lock duration
   - Impact on running app

Migration Diff:
${DIFF}"

codex exec --full-auto "${PROMPT}"
```

---

## Example 8: UI/UX Implementation Review

**Scenario**: Review UI component implementation

```bash
#!/bin/bash
PR_NUMBER=28

# Focus on UI files
UI_FILES=$(gh pr view ${PR_NUMBER} --repo Mega-Gorilla/USE-IT \
  --json files --jq '.files[] | select(.path | contains("gui") or contains("widgets")) | .path' | head -5)

PROMPT="UI/UX IMPLEMENTATION REVIEW - Assess:

1. **Accessibility**:
   - Keyboard navigation
   - Screen reader support
   - Color contrast
   - Focus indicators

2. **Responsive Design**:
   - Layout adaptability
   - Size constraints
   - Overflow handling

3. **User Feedback**:
   - Loading states
   - Error states
   - Success confirmation
   - Progress indication

4. **Consistency**:
   - Design system compliance
   - Component reuse
   - Naming conventions

5. **Usability**:
   - Intuitive interactions
   - Clear labels
   - Helpful error messages

Modified UI Files:
${UI_FILES}

PR Details: #${PR_NUMBER}"

codex exec --full-auto "${PROMPT}"
```

---

## Common Review Patterns

### Pattern 1: Multi-Stage Review

Review in multiple passes with different focus:

```bash
#!/bin/bash
ISSUE=27

echo "=== Pass 1: Architecture ==="
scripts/review_issue.sh ${ISSUE} # Default review

sleep 10

echo "=== Pass 2: Security ==="
# Custom security review script
./custom_security_review.sh ${ISSUE}

sleep 10

echo "=== Pass 3: Performance ==="
# Custom performance review script
./custom_performance_review.sh ${ISSUE}
```

### Pattern 2: Diff-Only Review

Review only code changes, not full context:

```bash
#!/bin/bash
PR=28

DIFF=$(gh pr diff ${PR} --repo Mega-Gorilla/USE-IT)

PROMPT="Code Review - Analyze only these changes:

${DIFF}

Focus on:
- Code quality
- Potential bugs
- Best practices"

codex exec --full-auto "${PROMPT}"
```

### Pattern 3: Conditional Review

Review based on file types:

```bash
#!/bin/bash
PR=$1

FILES=$(gh pr view ${PR} --repo Mega-Gorilla/USE-IT --json files --jq '.files[].path')

if echo "${FILES}" | grep -q "\.py$"; then
  echo "Python files detected - running Python review"
  # Python-specific review
fi

if echo "${FILES}" | grep -q "\.yaml$\|\.yml$"; then
  echo "YAML files detected - running config review"
  # Config file review
fi

if echo "${FILES}" | grep -q "test_"; then
  echo "Test files detected - running test review"
  # Test quality review
fi
```

---

## Troubleshooting Examples

### Debug: Check Token Usage

```bash
#!/bin/bash
ISSUE=27

OUTPUT=$(scripts/review_issue.sh ${ISSUE} 2>&1)

# Extract token count
TOKENS=$(echo "${OUTPUT}" | grep "tokens used" | awk '{print $3}')

echo "Tokens used: ${TOKENS}"

if [ "${TOKENS}" -gt 4000 ]; then
  echo "⚠️ High token usage - consider reducing context"
fi
```

### Debug: Save Raw Output

```bash
#!/bin/bash
ISSUE=27

# Run review and capture all output
scripts/review_issue.sh ${ISSUE} 2>&1 | tee "/tmp/review_${ISSUE}_$(date +%Y%m%d_%H%M%S).log"

echo "Log saved to /tmp/"
ls -lh /tmp/review_${ISSUE}_*.log | tail -1
```

### Debug: Dry Run (No Posting)

Modify script to skip GitHub posting:

```bash
#!/bin/bash
# Add DRY_RUN=1 support to review scripts

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY RUN MODE - Review content:"
  echo "${COMMENT_BODY}"
  exit 0
fi

# Normal flow...
```

Usage:
```bash
DRY_RUN=1 scripts/review_issue.sh 27
```

---

## Tips for Better Reviews

1. **Be Specific**: Focus prompts on specific concerns
2. **Provide Context**: Include relevant background information
3. **Iterate**: Refine prompts based on review quality
4. **Document Success**: Save effective prompts for reuse
5. **Monitor Costs**: Track token usage
6. **Combine Tools**: Use Codex + human review
7. **Respect Limits**: Don't overwhelm with reviews
8. **Quality Over Quantity**: One good review > many shallow reviews
