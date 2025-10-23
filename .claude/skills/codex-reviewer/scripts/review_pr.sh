#!/bin/bash
# Review GitHub Pull Request using OpenAI Codex
set -euo pipefail

REPO="Mega-Gorilla/USE-IT"
PR_NUMBER="${1:?Usage: $0 <pr_number>}"

echo "🔍 Fetching PR #${PR_NUMBER} from ${REPO}..."

# Check prerequisites
if ! command -v gh &>/dev/null; then
	echo "❌ Error: gh CLI not found. Install from: https://cli.github.com/"
	exit 1
fi

if ! command -v codex &>/dev/null; then
	echo "❌ Error: codex CLI not found. Install from: https://openai.com/codex"
	exit 1
fi

if ! command -v jq &>/dev/null; then
	echo "❌ Error: jq not found. Install with: sudo apt install jq"
	exit 1
fi

# Check if PR exists (ignore stderr warnings like Projects classic deprecation)
if ! gh pr view "${PR_NUMBER}" --repo "${REPO}" --json number >/dev/null 2>&1; then
	echo "❌ Error: PR #${PR_NUMBER} not found in ${REPO}"
	exit 1
fi

# Fetch PR content
PR_JSON=$(gh pr view "${PR_NUMBER}" --repo "${REPO}" \
	--json number,title,body,state,comments,commits,additions,deletions,author,headRefName,baseRefName 2>/dev/null)

if [ -z "${PR_JSON}" ]; then
	echo "❌ Error: Failed to fetch PR data"
	exit 1
fi

# Extract metadata
TITLE=$(echo "${PR_JSON}" | jq -r '.title')
BODY=$(echo "${PR_JSON}" | jq -r '.body // "No description provided"' | head -c 5000)
STATE=$(echo "${PR_JSON}" | jq -r '.state')
AUTHOR=$(echo "${PR_JSON}" | jq -r '.author.login')
HEAD=$(echo "${PR_JSON}" | jq -r '.headRefName')
BASE=$(echo "${PR_JSON}" | jq -r '.baseRefName')
ADDITIONS=$(echo "${PR_JSON}" | jq -r '.additions')
DELETIONS=$(echo "${PR_JSON}" | jq -r '.deletions')

# Format commits (limit to first 10)
COMMITS=$(echo "${PR_JSON}" | jq -r '
	.commits[0:10] |
	map("- \(.messageHeadline) (\(.oid[0:7]))") |
	join("\n")
')

# Format comments (limit to first 5)
COMMENTS=$(echo "${PR_JSON}" | jq -r '
	.comments[0:5] |
	map("**\(.author.login)**: \(.body | .[0:500])") |
	join("\n\n")
')

if [ -z "${COMMENTS}" ]; then
	COMMENTS="No comments yet."
fi

# Get diff summary (limit to first 100 lines to manage tokens)
echo "📄 Fetching diff..."
DIFF=$(gh pr diff "${PR_NUMBER}" --repo "${REPO}" 2>/dev/null | head -100 || echo "Diff unavailable or too large")

# Build review prompt
CONTEXT=$(cat <<EOF
# Pull Request #${PR_NUMBER}: ${TITLE}

**Author**: ${AUTHOR}
**Branch**: ${HEAD} → ${BASE}
**State**: ${STATE}
**Changes**: +${ADDITIONS} -${DELETIONS}

## Description

${BODY}

## Commits

${COMMITS}

## Code Changes (first 100 lines)

\`\`\`diff
${DIFF}
\`\`\`

## Discussion

${COMMENTS}

---

Please review this pull request thoroughly and provide your feedback in Japanese.

Consider the following aspects:
1. **Code Quality**: Is the code well-structured, readable, and maintainable?
2. **Potential Bugs**: Are there any obvious bugs, edge cases, or error-prone patterns?
3. **Performance**: Any performance concerns or optimization opportunities?
4. **Security**: Security vulnerabilities or best practice violations?
5. **Testing**: Is test coverage adequate? Are tests well-written?
6. **Architecture**: Does it follow project architecture and patterns?
7. **Best Practices**: Does it adhere to coding standards and best practices?
8. **Recommendations**: Specific, actionable improvements?

Provide your review in a clear, actionable format with reasoning and examples.
EOF
)

echo "🤖 Requesting Codex review..."
echo "⏳ This may take several minutes (max 20 min timeout)..."

# Request Codex review with timeout (20 minutes = 1200 seconds)
# Run in background and monitor progress
TEMP_OUTPUT="/tmp/codex_output_$$_$(date +%s).txt"
(timeout 1200 codex exec --full-auto "${CONTEXT}" 2>&1 || true) > "${TEMP_OUTPUT}" &
CODEX_PID=$!

# Monitor progress with 1-minute intervals
ELAPSED=0
INTERVAL=60
MAX_TIME=1200

while kill -0 "${CODEX_PID}" 2>/dev/null; do
	sleep ${INTERVAL}
	ELAPSED=$((ELAPSED + INTERVAL))
	MINUTES=$((ELAPSED / 60))
	echo "⏱️  Processing... ${MINUTES} minute(s) elapsed"

	if [ ${ELAPSED} -ge ${MAX_TIME} ]; then
		echo "⚠️  Timeout reached (20 minutes)"
		break
	fi
done

# Wait for completion and get exit code
wait "${CODEX_PID}" 2>/dev/null
EXIT_CODE=$?

# Read output
REVIEW_OUTPUT=$(cat "${TEMP_OUTPUT}")
rm -f "${TEMP_OUTPUT}"

# Check for timeout
if [ ${EXIT_CODE} -eq 124 ]; then
	echo "❌ Error: Codex review timed out after 20 minutes"
	echo "Try reducing the context size or reviewing a smaller issue/PR"
	exit 1
fi

if [ -z "${REVIEW_OUTPUT}" ]; then
	echo "❌ Error: No output received from Codex"
	exit 1
fi

# Extract actual review content
REVIEW=$(echo "${REVIEW_OUTPUT}" | sed -n '/^codex$/,/^tokens used$/p' | \
	sed '1d;$d' | sed '/^[[:space:]]*$/d')

if [ -z "${REVIEW}" ]; then
	echo "❌ Error: Empty review received from Codex"
	echo "Raw output:"
	echo "${REVIEW_OUTPUT}"
	exit 1
fi

# Extract token usage
TOKENS=$(echo "${REVIEW_OUTPUT}" | grep -A1 "^tokens used$" | tail -1 | tr -d ',' || echo "unknown")

echo "📊 Token usage: ${TOKENS}"
echo ""
echo "✍️ Posting review to GitHub..."

# Build comment body (just the review content, no extra formatting)
COMMENT_BODY="${REVIEW}"

# Save review to file as backup
BACKUP_FILE="/tmp/codex_review_pr_${PR_NUMBER}_$(date +%Y%m%d_%H%M%S).txt"
echo "${COMMENT_BODY}" > "${BACKUP_FILE}"
echo "💾 Review saved to: ${BACKUP_FILE}"

# Post review comment to GitHub
if gh pr comment "${PR_NUMBER}" --repo "${REPO}" --body "${COMMENT_BODY}" 2>/dev/null; then
	echo "✅ Review posted successfully!"
	echo "🔗 View at: https://github.com/${REPO}/pull/${PR_NUMBER}"
else
	echo "❌ Error: Failed to post comment to GitHub"
	echo "Review content is saved at: ${BACKUP_FILE}"
	echo "You can manually post it to GitHub."
	exit 1
fi
