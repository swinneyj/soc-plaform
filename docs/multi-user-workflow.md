### 🔄 Team Sync & Docker Feature Preview Workflow Guide

This document defines the absolute operational standard for **User A** and **User B** utilizing the .\git-sync.ps1 orchestration engine alongside local Docker branch sandboxing. 

### 🚀 1. The Core Golden Rules

1. 📁 **The Remote Master:** The Z:\ network drive (origin) remains the single source of absolute truth for code tracking.
2. 🌐 **The Preview Sandbox:** Pushing to local-gitea triggers your container ecosystem to dynamically build preview sandboxes on distinct ports.
3. 🌿 **Branch Naming:** **Strictly use hyphens instead of slashes** for all feature names (e.g., feature-xyz NOT feature/xyz) to keep Docker Compose project validation from failing.

### 🛠️ 2. Step-by-Step Execution Sequence

### Phase A: Create & Develop a Feature (You)

Always originate features from a clean, fully synchronized master tracking index. 

powershell

# 1. Ensure you are on clean main
git checkout main

# 2. Spin up your new feature branch (Remember: USE HYPHENS)
git checkout -b feature-xyz

# 3. Code / Work on your modifications...

# 4. Synchronize your progress locally
.\git-sync.ps1 -Branch feature-xyz -Message "What I changed"

Use code with caution.

### Phase B: Examine the Local Docker Preview Sandbox

Before passing your code or pushing it up to integration branches, verify it inside its isolated runtime box. 

powershell

# Push exclusively to your local container instance
git push local-gitea feature-xyz

Use code with caution.

* **What happens:** Look at the top of your console to grab your custom ports (e.g., 🌐 App Port: 9065). Open your browser to http://localhost:9065 to test.

### Phase C: Teammate Pulls & Edits the Feature

If a teammate needs to pull your work down to their machine to collaborate: 

powershell

# First Time retrieving the branch
git fetch origin
git checkout -b feature-xyz origin/feature-xyz

# Continuous work tracking
.\git-sync.ps1 -Branch feature-xyz -Message "Teammate's changes"

Use code with caution.

### 📦 3. Integration & Deployment Phase

### Phase D: Deploy Feature to Staging

When testing finishes and the feature is ready for integration verification: 

powershell

# 1. Switch to staging
git checkout staging

# 2. Fast-forward staging safely from the network remote
.\git-sync.ps1 -Branch staging -PreferRemote

# 3. Safely merge your tested sandbox feature in
.\git-sync.ps1 -Branch staging -MergeFrom feature-xyz -Message "Stage feature xyz"

Use code with caution.

### Phase E: Deploy Staging to Production (End of Day)

Once all combined team feature integrations pass staging validation, close out the day by moving the builds out to production: 

powershell

# 1. Switch back to your local master tracking branch
git checkout main

# 2. Bring down any upstream network adjustments
.\git-sync.ps1 -Branch main -PreferRemote

# 3. Push staging cleanly into production
.\git-sync.ps1 -Branch main -MergeFrom staging -Message "Staging -> main"

Use code with caution.

### 🧽 4. Local Workspace Refresh (When Needed)

Because your local manual container (soc_automation_working_clean) is frozen on whatever code version was present on your drive, you can force it to match your newly pulled network updates at any time by running: 

powershell

docker compose up -d --build --force-recreate

Use code with caution.