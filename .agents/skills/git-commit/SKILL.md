---
name: git-commit
description: Git commit workflow with GitHub issue linking - appending 'Closes #xxx' appropriately
---

## What I do

I ensure commits follow proper GitHub issue linking conventions, automatically appending issue references like `Closes #123` or `Fixes #456` when appropriate.

## When to use me

Use this when:
- Creating commits that resolve or relate to GitHub issues
- Working on features/fixes tracked in GitHub issues
- User mentions "commit" or asks to create commits
- User asks to push changes

## Key Principles

### 1. Always check for related issues before committing
When asked to commit, first check:
- Is there a GitHub issue this work addresses?
- Did the user reference an issue number?
- Is this a bug fix or feature completion?

### 2. Issue reference format
Append to commit message body (not title):
- `Closes #123` - when the commit fully resolves an issue
- `Fixes #456` - when fixing a bug tracked in an issue
- `Relates to #789` - when partially addressing or related to an issue
- `Implements #101` - when implementing a feature from an issue

### 3. Commit message structure
```
<short imperative summary>

<optional body explaining why>

Closes #xxx
```

Example:
```
Add user authentication middleware

Implements JWT-based auth for API endpoints with
token refresh support.

Closes #42
```

### 4. Multiple issues
When one commit addresses multiple issues:
```
Closes #123
Closes #456
```

### 5. When NOT to add issue references
- Pure refactoring with no issue
- Typo fixes or minor formatting
- User explicitly says "no issue link"
- No issue exists for the work

## Workflow

1. **Ask about issue linkage** if not mentioned:
   - "Is there a GitHub issue this commit should close?"
   - "What issue number should I reference?"

2. **Format the commit** with issue reference at end of body

3. **Confirm before committing** when uncertain:
   - "I'll commit with 'Closes #123' - is that correct?"

## Common Patterns

### Bug fix commit
```
Fix scheduler task deletion race condition

The task was being deleted while still running, causing
intermittent crashes. Added lock before deletion.

Fixes #89
```

### Feature implementation
```
Add memory search to bot commands

Users can now search stored memories via /search command.
Includes fuzzy matching and result ranking.

Closes #156
```

### Partial work
```
Refactor context store for thread safety

Part of the performance optimization effort. Extracts
connection pooling logic for reuse.

Relates to #201
```

## Repo-Specific Notes

- This project uses GitHub for issue tracking
- Check `.github/` for issue templates and contribution guidelines
- Prefer `Closes` over `Fixes` for feature work
- Prefer `Fixes` for bug fixes