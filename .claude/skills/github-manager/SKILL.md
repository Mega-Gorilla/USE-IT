---
name: github-manager
description: Manage GitHub issues and PRs using gh CLI: list, view, create, comment, edit, close issues; list, view, diff, review, approve PRs. Target USE-IT repository. Use when managing issues, PRs, posting comments, or checking project status.
allowed-tools: Bash
---

# GitHub Manager

Manage GitHub issues and pull requests for the USE-IT repository using `gh` CLI.

## Overview

This skill provides commands to:
- View and manage issues in the USE-IT project
- View and manage pull requests
- Post comments on issues and PRs
- Create new issues

**Target Repository**: `Mega-Gorilla/USE-IT` (always use `--repo` flag)

## Prerequisites

- `gh` CLI must be installed and authenticated
- Check authentication: `gh auth status`
- Required scopes: `repo`, `read:org`

## Issue Operations

### List Issues

**Command**:
```bash
gh issue list --repo Mega-Gorilla/USE-IT
```

**Options**:
- `--limit <n>`: Limit number of issues (default: 30)
- `--state open|closed|all`: Filter by state
- `--label <label>`: Filter by label

**Example Output**:
```
25	OPEN	Deprecate cloud and MCP components		2025-10-17T09:02:21Z
17	OPEN	LLMエラーメッセージの多言語化対応		2025-10-16T16:43:18Z
```

### View Issue Details

**Basic View**:
```bash
gh issue view <number> --repo Mega-Gorilla/USE-IT
```

**Detailed View with JSON**:
```bash
gh issue view <number> --repo Mega-Gorilla/USE-IT --json title,body,state,comments,author,createdAt,updatedAt
```

This returns structured data including:
- Issue title, body, and state
- All comments with author and timestamp
- Creation and update times

**Example**:
```bash
gh issue view 27 --repo Mega-Gorilla/USE-IT --json title,body,comments
```

### Get Issue Comments

**Method 1 - Using gh issue view**:
```bash
gh issue view <number> --repo Mega-Gorilla/USE-IT --json comments --jq '.comments[] | {author: .author.login, body, createdAt}'
```

**Method 2 - Using gh api**:
```bash
gh api repos/Mega-Gorilla/USE-IT/issues/<number>/comments --jq '.[] | {author: .user.login, created_at, body}'
```

Both methods return all comments with author information.

### Create Issue

```bash
gh issue create --repo Mega-Gorilla/USE-IT \
  --title "Issue title" \
  --body "Issue description"
```

**Optional Parameters**:
- `--label <label>`: Add labels
- `--assignee <username>`: Assign to user
- `--milestone <milestone>`: Add to milestone

### Comment on Issue

```bash
gh issue comment <number> --repo Mega-Gorilla/USE-IT \
  --body "Comment text"
```

### Edit Issue

```bash
gh issue edit <number> --repo Mega-Gorilla/USE-IT \
  --title "New title" \
  --body "New body"
```

### Close Issue

```bash
gh issue close <number> --repo Mega-Gorilla/USE-IT
```

## Pull Request Operations

### List Pull Requests

```bash
gh pr list --repo Mega-Gorilla/USE-IT
```

**Options**:
- `--limit <n>`: Limit number of PRs
- `--state open|closed|merged|all`: Filter by state
- `--base <branch>`: Filter by base branch

**Example Output**:
```
5	Close cached OpenRouter and Azure clients	fix/openrouter-azure-close	OPEN	2025-10-14T07:48:43Z
```

### View Pull Request Details

**Basic View**:
```bash
gh pr view <number> --repo Mega-Gorilla/USE-IT
```

**With Comments**:
```bash
gh pr view <number> --repo Mega-Gorilla/USE-IT --comments
```

**JSON Format**:
```bash
gh pr view <number> --repo Mega-Gorilla/USE-IT --json title,body,state,comments,commits
```

### View Pull Request Diff

```bash
gh pr diff <number> --repo Mega-Gorilla/USE-IT
```

### Comment on Pull Request

```bash
gh pr comment <number> --repo Mega-Gorilla/USE-IT \
  --body "Comment text"
```

### Review Pull Request

**Approve**:
```bash
gh pr review <number> --repo Mega-Gorilla/USE-IT --approve
```

**Request Changes**:
```bash
gh pr review <number> --repo Mega-Gorilla/USE-IT --request-changes \
  --body "Review comments"
```

**Comment Only**:
```bash
gh pr review <number> --repo Mega-Gorilla/USE-IT --comment \
  --body "Review comments"
```

## Important Notes

### Repository Context

- **ALWAYS** use `--repo Mega-Gorilla/USE-IT` flag
- This is a **FORK** repository (upstream: browser-use/browser-use)
- When creating issues, ensure they are for USE-IT, not upstream

### Warnings

- You may see "Projects (classic) is being deprecated" warnings
- These warnings can be **safely ignored** - data is still retrieved correctly
- The functionality remains unaffected

### Authentication

Verify authentication before operations:
```bash
gh auth status
```

Expected output should show:
- Logged in as: Mega-Gorilla
- Token scopes: repo, read:org (minimum required)

## Common Use Cases

### Check Recent Issues

```bash
gh issue list --repo Mega-Gorilla/USE-IT --limit 10
```

### View Issue with All Comments

```bash
gh issue view 27 --repo Mega-Gorilla/USE-IT --json title,body,state,comments
```

### Post Review Comment to Issue

```bash
gh issue comment 27 --repo Mega-Gorilla/USE-IT \
  --body "レビュー完了しました。変更内容を確認し、問題ありません。"
```

### Check Open Pull Requests

```bash
gh pr list --repo Mega-Gorilla/USE-IT --state open
```

### View PR Diff Before Review

```bash
gh pr diff 28 --repo Mega-Gorilla/USE-IT
```

### Approve Pull Request After Review

```bash
gh pr review 28 --repo Mega-Gorilla/USE-IT --approve \
  --body "LGTM! Changes look good."
```

## Troubleshooting

### Issue: "gh: command not found"
- Install gh CLI: https://cli.github.com/
- Or use package manager: `brew install gh` (macOS), `apt install gh` (Ubuntu)

### Issue: "authentication required"
- Run: `gh auth login`
- Follow prompts to authenticate

### Issue: "resource not accessible by personal access token"
- Check token scopes: `gh auth status`
- Token needs `repo` and `read:org` scopes
- Re-authenticate if necessary: `gh auth refresh -s repo,read:org`

## Tips

- Use JSON output for programmatic processing: `--json <fields>`
- Combine with `jq` for filtering: `--jq '<query>'`
- Use `--web` flag to open in browser: `gh issue view 27 --repo Mega-Gorilla/USE-IT --web`
- Check command help: `gh issue --help`, `gh pr --help`
