# Git Visual Map

This file explains the Git mental model for this repo.

Use it when you need to answer:

- When do I pull?
- When do I wait?
- Why should I avoid raw `git pull`?
- When should I commit and push?

## Core Mental Model

There are two Git states that matter most here:

1. `origin/main`
   The shared team baseline.
2. local `main`
   Your synced local baseline.

```mermaid
flowchart LR
    A[origin/main<br/>team baseline] --> B[local main<br/>synced local baseline]
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
    A[Coworker still pushing?] -->|Yes| B[Wait]
    B --> C[Validate local stack only]
    A -->|No| D[Run sync_upstream_safe.ps1 -AutoCheckpoint]
    D --> E{Conflict?}
    E -- Yes --> F[Resolve conflicted files only]
    F --> G[git add resolved files]
    G --> H[git commit]
    E -- No --> I[Run troubleshooter]
    H --> I
    I --> J[Run smoke test]
```

## Push Flow

```mermaid
flowchart TD
    A[Local stack passes smoke test] --> B[Run git-menu.ps1]
    B --> C[Commit changes on local main]
    C --> D[Push main]
```

## Decision Rules

- If a coworker is still pushing to `origin/main`, wait.
- If you have local edits, do not raw `git pull`.
- If you need the latest shared code, use `scripts\sync_upstream_safe.ps1 -AutoCheckpoint`.
- If you want to share your validated work, use `git-menu.ps1` from `main`.
- If a merge conflict happens, resolve only the direct overlap, then revalidate.

## Script Entry Points

- safe sync: `scripts\sync_upstream_safe.ps1 -AutoCheckpoint`
- validate local state: `scripts\troubleshoot_platform.ps1`
- validate live stack: `scripts\troubleshoot_platform.ps1 -RunSmokeTest`
- commit and push main: `git-menu.ps1`