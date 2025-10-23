---
name: codex-reviewer
description: Review GitHub issues and PRs using OpenAI Codex: analyze implementation feasibility, code quality, security, performance, architecture, test coverage. Post automated review comments to USE-IT repository. Use when reviewing issues, PRs, or requesting AI code review.
allowed-tools: Bash
---

# Codex Reviewer

AI-powered code and implementation review using OpenAI Codex.

## Overview

This skill automates the review process by:
1. Fetching issue/PR content from GitHub (title, body, comments, commits)
2. Requesting comprehensive review from OpenAI Codex
3. Posting review results as GitHub comments

**Target Repository**: `Mega-Gorilla/USE-IT`

## Prerequisites

- `gh` CLI authenticated (`gh auth status`)
- `codex` CLI authenticated (`codex login`)
- `jq` installed for JSON processing

## Quick Start

### Review Issue

```bash
scripts/review_issue.sh <issue_number>
```

Example:
```bash
scripts/review_issue.sh 27
```

### Review Pull Request

```bash
scripts/review_pr.sh <pr_number>
```

Example:
```bash
scripts/review_pr.sh 28
```

## What Gets Reviewed

### For Issues
- Implementation plan feasibility
- Missing considerations
- Security concerns
- Architecture suggestions
- Edge cases
- Best practices

### For Pull Requests
- Code quality
- Potential bugs
- Performance implications
- Security vulnerabilities
- Best practices adherence
- Test coverage
- Architecture consistency

## Output

Review results are posted as GitHub comments with:
- **Review Summary**: High-level findings
- **Detailed Analysis**: Point-by-point feedback
- **Recommendations**: Actionable improvements
- **Automated Tag**: `🤖 Automated review by OpenAI Codex`

## Advanced Usage

See reference.md for:
- Custom review prompts
- Codex model selection
- Sandbox configuration
- Token management

See examples.md for:
- Common review scenarios
- Custom prompt examples
- Troubleshooting tips

## Important Notes

- Codex reviews are suggestions, not requirements
- Human review is still recommended for critical changes
- Token costs apply for Codex API usage
- Review quality depends on context quality
- Large PRs may exceed token limits (diff is truncated to 100 lines)
- **Timeout**: Reviews automatically timeout after 20 minutes
- **Progress**: Status updates displayed every minute during processing

## Troubleshooting

### Issue: "codex: command not found"
Install Codex CLI from: https://openai.com/codex

### Issue: "authentication required"
Run: `codex login`

### Issue: Empty review received
- Check token limits
- Verify prompt formatting
- Review raw Codex output
- Check Codex authentication status

### Issue: GitHub comment failed
Review content is saved to `/tmp/review_<number>.txt` for manual posting.
