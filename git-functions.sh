#!/bin/bash

# bcpp: Branch + Commit + Push + PR + Open
bcpp() {
    if [ $# -eq 0 ]; then
        echo "❌ Usage: bcpp <commit message>"
        echo "📝 Example: bcpp hello world"
        return 1
    fi
    
    # Check if gh CLI is installed
    if ! command -v gh &> /dev/null; then
        echo "❌ GitHub CLI (gh) is required. Install: https://cli.github.com/"
        return 1
    fi
    
    local commit_msg="$*"
    local branch_name=$(echo "$commit_msg" | tr '[:upper:]' '[:lower:]' | sed 's/ /-/g' | sed 's/[^a-z0-9-]//g')
    
    # Check if branch already exists
    if git show-ref --verify --quiet refs/heads/"$branch_name"; then
        echo "❌ Branch '$branch_name' already exists!"
        echo "💡 Try a different commit message or delete the existing branch"
        return 1
    fi
    
    echo "🌿 Creating branch: $branch_name"
    git checkout -b "$branch_name" || return 1
    
    echo "📝 Adding all changes..."
    git add . || return 1
    
    # Check if there are changes to commit
    if git diff --staged --quiet; then
        echo "ℹ️  No changes to commit"
        git checkout - && git branch -d "$branch_name"
        return 1
    fi
    
    echo "💾 Committing: $commit_msg"
    git commit -m "$commit_msg" || return 1
    
    echo "🚀 Pushing branch..."
    git push -u origin "$branch_name" || return 1
    
    echo "�� Creating PR..."
    gh pr create --title "$commit_msg" --body "Auto-generated PR for: $commit_msg" --head "$branch_name" || return 1
    
    echo "🌐 Opening PR in browser..."
    gh pr view --web
    
    echo "✅ Done! Branch: $branch_name"
}

# cpp: Commit + Push + PR (no new branch)
cpp() {
    if [ $# -eq 0 ]; then
        echo "❌ Usage: cpp <commit message>"
        echo "📝 Example: cpp hello world"
        return 1
    fi
    
    local commit_msg="$*"
    local current_branch=$(git branch --show-current)
    
    echo "📝 Adding all changes..."
    git add . || return 1
    
    # Check if there are changes to commit
    if git diff --staged --quiet; then
        echo "ℹ️  No changes to commit"
        return 1
    fi
    
    echo "💾 Committing to $current_branch: $commit_msg"
    git commit -m "$commit_msg" || return 1
    
    echo "🚀 Pushing to $current_branch..."
    git push || return 1
    
    # Only create PR if not on main/master and gh CLI is available
    if [[ "$current_branch" != "main" && "$current_branch" != "master" ]] && command -v gh &> /dev/null; then
        echo "🔄 Creating/updating PR from $current_branch..."
        
        # Check if PR already exists
        if gh pr view --json number &> /dev/null; then
            echo "ℹ️  PR already exists, updated with new commit"
            gh pr view --web
        else
            gh pr create --title "$commit_msg" --body "Auto-generated PR for: $commit_msg" --head "$current_branch"
            echo "🌐 Opening PR in browser..."
            gh pr view --web
        fi
    else
        echo "ℹ️  On main branch or gh CLI not available, skipping PR creation"
    fi
    
    echo "✅ Done!"
}

# Quick help function
git-help() {
    echo "🚀 Git Automation Functions:"
    echo ""
    echo "  bcpp <message>  - Branch + Commit + Push + PR"
    echo "                    Creates new branch, commits, pushes, creates PR"
    echo "                    Example: bcpp fix login bug"
    echo ""
    echo "  cpp <message>   - Commit + Push + PR (current branch)"
    echo "                    Commits to current branch, pushes, creates/updates PR"
    echo "                    Example: cpp update documentation"
    echo ""
    echo "💡 Both functions automatically open the PR in your browser"
}

echo "🎉 Git automation functions loaded! Type 'git-help' for usage info."
