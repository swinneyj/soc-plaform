Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $RepoRoot 'scripts\sync_upstream_safe.ps1'

function Show-Status {
    Write-Host "`n--- Current Local Status ---" -ForegroundColor Yellow
    git status -sb
    Write-Host "`n--- Remotes ---" -ForegroundColor Yellow
    git remote -v
    Write-Host "----------------------------`n" -ForegroundColor Yellow
}

function Wait-ForUser {
    Read-Host "Press Enter to return to the menu" | Out-Null
}

function Get-CurrentBranch {
    return (git branch --show-current).Trim()
}

function ConvertTo-BranchSlug {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ''
    }

    $slug = $Text.ToLowerInvariant()
    $slug = $slug -replace '[^a-z0-9]+', '-'
    $slug = $slug.Trim('-')
    return $slug
}

function Ensure-WorkingBranch {
    $currentBranch = Get-CurrentBranch

    if ([string]::IsNullOrWhiteSpace($currentBranch)) {
        Write-Warning "Could not determine the current branch."
        Wait-ForUser
        return $null
    }

    if ($currentBranch -ne 'main') {
        return $currentBranch
    }

    Write-Host "`n[!] Direct pushes from 'main' are blocked." -ForegroundColor Yellow
    Write-Host "    Create a temporary branch first, test there, and push that branch instead." -ForegroundColor Yellow

    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $topic = Read-Host "Enter a short topic for the branch (optional, e.g. db-repro or docker-cleanup)"
    $topicSlug = ConvertTo-BranchSlug -Text $topic

    if ([string]::IsNullOrWhiteSpace($topicSlug)) {
        $defaultBranchName = "agent-lewis/{0}" -f $timestamp
    } else {
        $defaultBranchName = "agent-lewis/{0}-{1}" -f $topicSlug, $timestamp
    }

    $branchName = Read-Host "Enter a temporary branch name (or press Enter for '$defaultBranchName')"
    if ([string]::IsNullOrWhiteSpace($branchName)) {
        $branchName = $defaultBranchName
    }

    git switch -c $branchName
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not create branch '$branchName'. Push aborted."
        Wait-ForUser
        return $null
    }

    Write-Host "`n[+] Switched to temporary branch '$branchName'." -ForegroundColor Green
    return $branchName
}

function Invoke-PullWorkflow {
    if (-not (Test-Path $SyncScript)) {
        Write-Warning "Sync helper not found: $SyncScript"
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Pulling latest changes into this working copy..." -ForegroundColor Cyan
    Write-Host "    A local checkpoint commit will be created first if your tree is dirty." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -AutoCheckpoint

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Pull workflow completed." -ForegroundColor Green
    } else {
        Write-Warning "Pull workflow stopped. Review the output above for conflicts or validation errors."
    }

    Wait-ForUser
}

function Invoke-PushWorkflow {
    Write-Host "`n[+] Preparing to push changes..." -ForegroundColor Cyan
    $branchName = Ensure-WorkingBranch
    if (-not $branchName) {
        return
    }

    $commitMsg = Read-Host "Enter a short description of your changes (or press Enter for 'Update SOC Platform files')"
    if ([string]::IsNullOrWhiteSpace($commitMsg)) {
        $commitMsg = 'Update SOC Platform files'
    }

    git add -A
    git commit -m $commitMsg
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Commit did not complete. Push aborted."
        Wait-ForUser
        return
    }

    git push -u origin $branchName
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Successfully pushed branch '$branchName'." -ForegroundColor Green
    } else {
        Write-Warning "Push failed. Review the git output above."
    }

    Wait-ForUser
}

# --- Main Menu Loop ---
while ($true) {
    Write-Host "`n=================================" -ForegroundColor Cyan
    Write-Host "      SOC WORKING COPY SYNC      " -ForegroundColor Cyan
    Write-Host "=================================" -ForegroundColor Cyan

    Show-Status

    Write-Host "1. Pull latest safely (recommended)"
    Write-Host "2. Push current branch (main blocked)"
    Write-Host "3. Exit"
    Write-Host "=================================" -ForegroundColor Cyan

    $choice = Read-Host "Select an option (1-3)"

    switch ($choice) {
        '1' { Invoke-PullWorkflow }
        '2' { Invoke-PushWorkflow }
        '3' { exit }
        default { Write-Warning "Invalid selection. Please choose 1-3." }
    }
}
