#!/bin/bash
# Review GitHub Issue using OpenAI Codex
set -euo pipefail

REPO="Mega-Gorilla/USE-IT"
ISSUE_NUMBER="${1:?Usage: $0 <issue_number>}"

echo "🔍 Fetching Issue #${ISSUE_NUMBER} from ${REPO}..."

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

# Check if issue exists (ignore stderr warnings like Projects classic deprecation)
if ! gh issue view "${ISSUE_NUMBER}" --repo "${REPO}" --json number >/dev/null 2>&1; then
	echo "❌ Error: Issue #${ISSUE_NUMBER} not found in ${REPO}"
	exit 1
fi

# Fetch issue content
ISSUE_JSON=$(gh issue view "${ISSUE_NUMBER}" --repo "${REPO}" \
	--json number,title,body,state,comments,author,createdAt,labels 2>/dev/null)

if [ -z "${ISSUE_JSON}" ]; then
	echo "❌ Error: Failed to fetch issue data"
	exit 1
fi

# Extract and format context
TITLE=$(echo "${ISSUE_JSON}" | jq -r '.title')
BODY=$(echo "${ISSUE_JSON}" | jq -r '.body // "No description provided"' | head -c 5000)
STATE=$(echo "${ISSUE_JSON}" | jq -r '.state')
AUTHOR=$(echo "${ISSUE_JSON}" | jq -r '.author.login')
CREATED=$(echo "${ISSUE_JSON}" | jq -r '.createdAt | split("T")[0]')
LABELS=$(echo "${ISSUE_JSON}" | jq -r '.labels[]?.name' | paste -sd ',' - || echo "none")

# Format comments (limit to prevent token overflow)
COMMENTS=$(echo "${ISSUE_JSON}" | jq -r '
	.comments[0:5] |
	map("**\(.author.login)** (\(.createdAt | split("T")[0])):\n\(.body | .[0:500])\n") |
	join("\n")
')

if [ -z "${COMMENTS}" ]; then
	COMMENTS="No comments yet."
fi

# Build review prompt
CONTEXT=$(cat <<EOF
# Issue #${ISSUE_NUMBER}: ${TITLE}

**Author**: ${AUTHOR}
**Created**: ${CREATED}
**State**: ${STATE}
**Labels**: ${LABELS}

## Description

${BODY}

## Discussion

${COMMENTS}

---

Please review this issue thoroughly and provide your feedback in Japanese.

Consider the following aspects:
1. **Implementation Feasibility**: Is the proposed approach viable and well-thought-out?
2. **Missing Considerations**: What important aspects or edge cases are overlooked?
3. **Architecture**: Are there better architectural approaches or patterns?
4. **Security**: Any security concerns or vulnerabilities?
5. **Best Practices**: Does it follow software engineering best practices?
6. **Recommendations**: What specific improvements or changes do you suggest?

Provide your review in a clear, actionable format with reasoning.
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

# Extract actual review content (between "codex" and "tokens used")
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
BACKUP_FILE="/tmp/codex_review_issue_${ISSUE_NUMBER}_$(date +%Y%m%d_%H%M%S).txt"
echo "${COMMENT_BODY}" > "${BACKUP_FILE}"
echo "💾 Review saved to: ${BACKUP_FILE}"

# Post review comment to GitHub
if gh issue comment "${ISSUE_NUMBER}" --repo "${REPO}" --body "${COMMENT_BODY}" 2>/dev/null; then
	echo "✅ Review posted successfully!"
	echo "🔗 View at: https://github.com/${REPO}/issues/${ISSUE_NUMBER}"
else
	echo "❌ Error: Failed to post comment to GitHub"
	echo "Review content is saved at: ${BACKUP_FILE}"
	echo "You can manually post it to GitHub."
	exit 1
fi
