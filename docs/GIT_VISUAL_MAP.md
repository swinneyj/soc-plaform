# Git Visual Map

This file explains the Git mental model for this repo.

Use it when you need to answer:

- When do I pull?
- When do I wait?
- Why should I avoid raw `git pull`?
- When should I commit and push?

## Core Mental Model

There are four Git roles that matter here:

1. `main`
    The protected baseline. Do not do normal coding here.
2. work/staging branches
    These are the branches where people actively code.
3. `staging`
    The one configured demo branch used to combine selected work for the meeting.
4. `main promotion`
    The explicit final step after the demo branch is validated.

```mermaid
flowchart LR
    A[origin/main<br/>protected baseline] --> B[local main]
    B --> C[work/staging branch A]
    B --> D[work/staging branch B]
    B --> E[work/staging branch C]
    C --> F[origin/work branch A]
    D --> G[origin/work branch B]
    E --> H[origin/work branch C]
    F --> I[origin/staging<br/>one configured demo branch]
    G --> I
    H --> I
    I --> J[origin/main<br/>final approved merge]
```

## Why Raw Pull Is Risky Here

This repo often has:

- local uncommitted edits
- coworker pushes landing on `origin/main`
- runtime and docs changes mixed together

So the correct mental model is:

```mermaid
flowchart TD
    A[Have local edits?] -->|Yes| B[Do not raw git pull]
    B --> C[Use sync_upstream_safe.ps1 -AutoCheckpoint]
    A -->|No| D[Can pull more safely]
```

## Safe Team Sync Flow

```mermaid
flowchart TD
    A[Need latest main?] --> B[Sync local main from origin/main]
    B --> C[Create or switch to a work/staging branch]
    C --> D[Commit and push that branch]
    D --> E[Choose branches for the meeting]
    E --> F[Merge selected branches into the one demo branch]
    F --> G[Validate the demo branch]
    G --> H[Merge the approved demo branch into main]
```

## Menu Flow

```mermaid
flowchart TD
    A[Run git-menu.ps1] --> B[Refresh main]
    B --> C[Open or create a work branch]
    C --> D[Push current work branch]
    D --> E[Open the configured demo branch]
    E --> F[Bring selected work into the demo branch]
    F --> G[Ship the approved demo branch to main]
```

## Decision Rules

- Do not do normal coding on `main`.
- If you have local edits, do not raw `git pull`.
- If you need the latest shared code, use `scripts\sync_upstream_safe.ps1 -AutoCheckpoint`.
- If you are doing active work, do it on a work/staging branch, not on `main`.
- Only use the one configured demo branch to combine selected work for the meeting.
- Only update `main` through the approved demo branch.
- If a merge conflict happens, resolve only the direct overlap, then revalidate.

## Script Entry Points

- safe sync: `scripts\sync_upstream_safe.ps1 -AutoCheckpoint`
- validate local state: `scripts\troubleshoot_platform.ps1`
- validate live stack: `scripts\troubleshoot_platform.ps1 -RunSmokeTest`
- branch-aware workflow menu: `git-menu.ps1`
- commit, push, or merge a target branch: `git-sync.ps1`