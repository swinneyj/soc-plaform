param (
    [string]$Message = "",  # auto-filled below if not provided
    [string]$Branch = "main",
    [switch]$All
)

function Invoke-GitSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string]$ErrorMessage = "Git command failed."
    )

    Write-Host "[git] $Command" -ForegroundColor DarkCyan
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage ExitCode=$LASTEXITCODE"
    }
}

try {
    # Ensure we're in a Git repo
    $status = git status 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "This folder is not a Git repository."
    }

    # Determine branch: if not explicitly provided, default to current branch
    $currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()
    if (-not $PSBoundParameters.ContainsKey('Branch') -or -not $Branch) {
        $inputBranch = Read-Host "Branch to sync (default: $currentBranch)"
        if ([string]::IsNullOrWhiteSpace($inputBranch)) {
            $Branch = $currentBranch
        } else {
            $Branch = $inputBranch.Trim()
        }
    }

    # Self-heal if Git reports a detached HEAD or in-progress rebase/merge
    if ($currentBranch -eq 'HEAD') {
        Write-Warning "Git reports HEAD (detached or in-progress rebase/merge). Attempting automatic recovery..."

        # Try aborting any rebase first
        git rebase --abort 2>$null
        if ($LASTEXITCODE -ne 0) {
            # If that didn't work, try aborting a merge
            git merge --abort 2>$null
        }

        # Re-detect current branch after attempted recovery
        $currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()

        # If still HEAD but a target branch was provided, try switching to it
        if ($currentBranch -eq 'HEAD' -and $Branch) {
            Write-Host "[*] Switching to branch '$Branch'..." -ForegroundColor Cyan
            Invoke-GitSafe -Command "git checkout $Branch" -ErrorMessage "Failed to switch to branch '$Branch'. Resolve Git state manually."
            $currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()
        }
    }

    if ($currentBranch -ne $Branch) {
        throw "Refusing to sync from branch '$currentBranch'. Expected '$Branch'. Switch branches or use -Branch to override intentionally."
    }

    # If no commit message was provided, generate a timestamped default.
    if (-not $PSBoundParameters.ContainsKey('Message') -or -not $Message) {
        $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
        $Message = "SOC sync - $timestamp"
    }

    # Optionally limit staged files in the future; for now, stage all tracked + new files
    Write-Host "[*] Staging changes..." -ForegroundColor Cyan
    if ($All) {
        Invoke-GitSafe -Command "git add -A" -ErrorMessage "Failed to stage changes."
    } else {
        Invoke-GitSafe -Command "git add ." -ErrorMessage "Failed to stage changes."
    }

    # Commit if there is anything to commit
    Write-Host "[*] Checking for changes to commit..." -ForegroundColor Cyan
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[+] No staged changes to commit." -ForegroundColor Green
    }
    else {
        Write-Host "[*] Committing changes..." -ForegroundColor Cyan
        Invoke-GitSafe -Command "git commit -m `"$Message`"" -ErrorMessage "Git commit failed."
    }

    # Always attempt to push, even if this run had nothing new to commit,
    # so that previously-created local commits still get synced.
    Write-Host "[*] Pushing changes to origin/$Branch..." -ForegroundColor Cyan
    git push origin $Branch
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[+] Push successful." -ForegroundColor Green
        return
    }

    Write-Warning "Initial push failed. Attempting to pull and re-push (rebase)..."

    # Try to reconcile with remote via pull --rebase. If this hits conflicts,
    # automatically abort the rebase so the repo is not left in a stuck state.
    try {
        Invoke-GitSafe -Command "git pull --rebase origin $Branch" -ErrorMessage "git pull --rebase failed."
    }
    catch {
        Write-Warning "git pull --rebase encountered conflicts. Aborting rebase to restore previous state."
        git rebase --abort 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "git rebase --abort did not complete cleanly. Repository may still be mid-rebase; resolve manually."
        }

        Write-Warning "Remote contains conflicting changes. Review and resolve conflicts (e.g., by pulling and merging manually), then rerun git-sync.ps1."
        throw $_
    }

    Write-Host "[*] Retrying push after rebase..." -ForegroundColor Cyan
    Invoke-GitSafe -Command "git push origin $Branch" -ErrorMessage "Push failed even after rebase. Resolve Git issues manually."

    Write-Host "[+] Push successful after rebase." -ForegroundColor Green
}
catch {
    Write-Warning $_.Exception.Message
    exit 1
}