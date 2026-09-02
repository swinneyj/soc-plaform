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
    The stable baseline.
2. `feature/*`
    The branch where active work happens.
3. `staging`
    The integration checkpoint.
4. `main promotion`
    The explicit final step after staging is validated.

```mermaid
flowchart LR
     A[origin/main<br/>stable baseline] --> B[local main]
     B --> C[local feature/*<br/>active work]
     C --> D[origin/feature/*]
     D --> E[origin/staging<br/>integration checkpoint]
     E --> F[local staging]
     F --> G[origin/main<br/>explicit promotion]
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
    B --> C[Create or switch to feature branch]
    C --> D[Commit and push feature branch]
    D --> E[Merge feature into staging]
    E --> F[Validate staging]
    F --> G[Promote staging into main]
```

## Menu Flow

```mermaid
flowchart TD
    A[Run git-menu.ps1] --> B[Sync main or staging]
    B --> C[Create feature branch]
    C --> D[Push current branch]
    D --> E[Merge feature into staging]
    E --> F[Promote staging into main]
```

## Decision Rules

- If a coworker is still pushing to `origin/main`, wait.
- If you have local edits, do not raw `git pull`.
- If you need the latest shared code, use `scripts\sync_upstream_safe.ps1 -AutoCheckpoint`.
- If you are doing active work, do it on `feature/*`, not on `main`.
- If you want to validate integration, merge feature work into `staging` first.
- Only update `main` through explicit staging promotion.
- If a merge conflict happens, resolve only the direct overlap, then revalidate.

## Script Entry Points

- safe sync: `scripts\sync_upstream_safe.ps1 -AutoCheckpoint`
- validate local state: `scripts\troubleshoot_platform.ps1`
- validate live stack: `scripts\troubleshoot_platform.ps1 -RunSmokeTest`
- branch-aware workflow menu: `git-menu.ps1`
- commit, push, or merge a target branch: `git-sync.ps1`