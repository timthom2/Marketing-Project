#!/usr/bin/env python3
"""Monitor PR for Codex review comments."""
import json
import time
import urllib.request
import urllib.error
import subprocess
import sys
from datetime import datetime, timedelta


def get_repo_info():
    """Get repository owner and name from git remote."""
    result = subprocess.run(['git', 'remote', 'get-url', 'origin'], capture_output=True, text=True)
    remote_url = result.stdout.strip()
    parts = remote_url.replace('https://github.com/', '').replace('.git', '').split('/')
    return parts[0], parts[1]


def check_pr_exists(owner, repo, head_branch):
    """Check if PR exists for the given branch."""
    url = f'https://api.github.com/repos/{owner}/{repo}/pulls?head={owner}:{head_branch}&state=open'
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req) as response:
            prs = json.loads(response.read().decode('utf-8'))
            return prs[0] if prs else None
    except Exception as e:
        print(f"Error checking for PR: {e}")
        return None


def get_pr_comments(owner, repo, pr_number):
    """Get all comments for a PR."""
    url = f'https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments'
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching comments: {e}")
        return []


def get_issue_comments(owner, repo, pr_number):
    """Get issue comments (review comments are separate from review comments)."""
    url = f'https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments'
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching issue comments: {e}")
        return []


def is_codex_comment(comment):
    """Check if comment is from Codex (heuristic: look for common patterns)."""
    author = comment.get('user', {}).get('login', '').lower()
    body = comment.get('body', '').lower()
    
    # Check for Codex bot patterns
    codex_indicators = [
        'codex',
        'ai review',
        'automated review',
        'suggested change',
        'consider',
        'recommendation'
    ]
    
    # Check author (common bot names)
    if 'bot' in author or 'codex' in author:
        return True
    
    # Check comment body for review patterns
    if any(indicator in body for indicator in codex_indicators):
        return True
    
    return False


def monitor_pr_review(owner, repo, head_branch, wait_initial=240, check_interval=60, max_checks=10):
    """Monitor PR for Codex review comments.
    
    Args:
        owner: GitHub repo owner
        repo: Repository name
        head_branch: Branch name to check for PR
        wait_initial: Initial wait time in seconds (default 4 minutes)
        check_interval: Interval between checks in seconds (default 1 minute)
        max_checks: Maximum number of checks after initial wait
    """
    print(f"Monitoring PR for branch: {head_branch}")
    print(f"Repository: {owner}/{repo}")
    print(f"Initial wait: {wait_initial}s ({wait_initial//60} minutes)")
    print(f"Check interval: {check_interval}s ({check_interval} seconds)")
    print(f"Max checks: {max_checks}")
    print("-" * 60)
    
    # Step 1: Wait for PR to be created
    print("\n[Step 1] Waiting for PR to be created...")
    pr = None
    check_count = 0
    max_pr_checks = 30  # Check for PR for up to 5 minutes (30 * 10s)
    
    while not pr and check_count < max_pr_checks:
        pr = check_pr_exists(owner, repo, head_branch)
        if pr:
            print(f"✅ PR found: {pr['html_url']}")
            print(f"   PR #{pr['number']}: {pr['title']}")
            break
        check_count += 1
        if check_count < max_pr_checks:
            time.sleep(10)  # Check every 10 seconds for PR creation
            print(f"   Checking for PR... ({check_count}/{max_pr_checks})")
    
    if not pr:
        print("❌ PR not found after waiting. Please create PR manually.")
        print(f"   URL: https://github.com/{owner}/{repo}/compare/review-workflow-updates...{head_branch}")
        return None
    
    pr_number = pr['number']
    print(f"\n[Step 2] Monitoring PR #{pr_number} for Codex comments...")
    
    # Step 2: Initial wait (4 minutes)
    print(f"   Waiting {wait_initial}s ({wait_initial//60} minutes) for initial review...")
    time.sleep(wait_initial)
    
    # Step 3: Check for comments periodically
    all_comments = []
    codex_comments = []
    check_count = 0
    
    print(f"\n[Step 3] Checking for comments every {check_interval}s...")
    
    while check_count < max_checks:
        check_count += 1
        print(f"\n   Check #{check_count}/{max_checks} at {datetime.now().strftime('%H:%M:%S')}")
        
        # Get all comments
        pr_comments = get_pr_comments(owner, repo, pr_number)
        issue_comments = get_issue_comments(owner, repo, pr_number)
        current_comments = pr_comments + issue_comments
        
        # Track new comments
        existing_ids = {c['id'] for c in all_comments}
        new_comments = [c for c in current_comments if c['id'] not in existing_ids]
        
        if new_comments:
            print(f"   Found {len(new_comments)} new comment(s)")
            for comment in new_comments:
                author = comment.get('user', {}).get('login', 'unknown')
                created = comment.get('created_at', '')
                body_preview = comment.get('body', '')[:100].replace('\n', ' ')
                print(f"   - {author} at {created}: {body_preview}...")
                
                if is_codex_comment(comment):
                    codex_comments.append(comment)
                    print(f"     ⚠️  Potential Codex comment detected!")
        
        all_comments = current_comments
        
        if codex_comments:
            print(f"\n✅ Found {len(codex_comments)} Codex comment(s)!")
            break
        
        if check_count < max_checks:
            time.sleep(check_interval)
    
    # Summary
    print("\n" + "=" * 60)
    print("MONITORING SUMMARY")
    print("=" * 60)
    print(f"PR: {pr['html_url']}")
    print(f"Total comments found: {len(all_comments)}")
    print(f"Codex comments found: {len(codex_comments)}")
    
    if codex_comments:
        print("\nCodex Comments:")
        for i, comment in enumerate(codex_comments, 1):
            print(f"\n  {i}. {comment.get('user', {}).get('login', 'unknown')}")
            print(f"     Created: {comment.get('created_at', '')}")
            print(f"     Body: {comment.get('body', '')[:200]}...")
            print(f"     URL: {comment.get('html_url', '')}")
    else:
        print("\n⚠️  No Codex comments detected after monitoring period.")
        print("   This could mean:")
        print("   - Codex hasn't reviewed yet")
        print("   - Codex review is disabled")
        print("   - Comments don't match detection patterns")
    
    return {
        'pr': pr,
        'total_comments': len(all_comments),
        'codex_comments': codex_comments
    }


if __name__ == '__main__':
    owner, repo = get_repo_info()
    head_branch = 'pr-review-workflow-updates'
    
    result = monitor_pr_review(owner, repo, head_branch)
    
    if result:
        sys.exit(0)
    else:
        sys.exit(1)

