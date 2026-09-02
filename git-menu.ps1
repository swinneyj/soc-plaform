Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $RepoRoot 'scripts\sync_upstream_safe.ps1'
$PushScript = Join-Path $RepoRoot 'git-sync.ps1'

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Args
    )

    & git @Args
    return $LASTEXITCODE
}

function Show-Status {
    $currentBranch = Get-CurrentBranch
    $upstreamBranch = Get-UpstreamBranch
    $changeCount = @(git status --porcelain).Count
    $role = Get-BranchRole -BranchName $currentBranch

    Write-Host "" 
    Write-Host ("You are on : {0}" -f $currentBranch) -ForegroundColor Yellow
    Write-Host ("Tracks     : {0}" -f $upstreamBranch) -ForegroundColor Yellow
    Write-Host ("Role       : {0}" -f $role) -ForegroundColor Yellow
    Write-Host ("Changes    : {0} local file(s) modified" -f $changeCount) -ForegroundColor Yellow
    Write-Host "" 
}

function Show-DetailedStatus {
    Write-Host "`n--- Current Local Status ---" -ForegroundColor Yellow
    git status -sb
    Write-Host "`n--- Remotes ---" -ForegroundColor Yellow
    git remote -v
    Write-Host "----------------------------`n" -ForegroundColor Yellow
    Wait-ForUser
}

function Wait-ForUser {
    Read-Host "Press Enter to return to the menu" | Out-Null
}

function Get-CurrentBranch {
    return (git branch --show-current).Trim()
}

function Get-UpstreamBranch {
    $upstream = git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($upstream)) {
        return '(none)'
    }

    return $upstream.Trim()
}

function Get-BranchRole {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    switch -Regex ($BranchName) {
        '^main$' { return 'stable baseline' }
        '^staging$' { return 'integration checkpoint' }
        '^feature/' { return 'active feature work' }
        default { return 'other branch' }
    }
}

function Test-LocalBranchExists {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    & git show-ref --verify --quiet "refs/heads/$BranchName"
    return ($LASTEXITCODE -eq 0)
}

function Test-RemoteBranchExists {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    & git fetch origin $BranchName 2>$null | Out-Null
    & git show-ref --verify --quiet "refs/remotes/origin/$BranchName"
    return ($LASTEXITCODE -eq 0)
}

function Test-WorkingTreeDirty {
    return (@(git status --porcelain).Count -gt 0)
}

function Ensure-BranchCheckedOut {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    $currentBranch = Get-CurrentBranch
    if ($currentBranch -eq $BranchName) {
        return $true
    }

    if (Test-LocalBranchExists -BranchName $BranchName) {
        if ((Invoke-GitCommand -Args @('checkout', $BranchName)) -eq 0) {
            return $true
        }

        if (Test-WorkingTreeDirty) {
            Write-Warning "Could not switch to '$BranchName' because local changes would be overwritten. Commit or stash your current changes first."
            return $false
        }

        Write-Warning "Could not switch to existing local branch '$BranchName'. Review the git output above."
        return $false
    }

    if (Test-RemoteBranchExists -BranchName $BranchName) {
        if ((Invoke-GitCommand -Args @('checkout', '-b', $BranchName, "origin/$BranchName")) -eq 0) {
            return $true
        }

        if (Test-WorkingTreeDirty) {
            Write-Warning "Could not create local '$BranchName' from origin/$BranchName because local changes would be overwritten. Commit or stash your current changes first."
            return $false
        }

        Write-Warning "origin/$BranchName exists, but the local checkout failed. Review the git output above."
        return $false
    }

    Write-Warning "Branch '$BranchName' does not exist locally or on origin."
    return $false
}

function Invoke-SafeSyncWorkflow {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    if (-not (Test-Path $SyncScript)) {
        Write-Warning "Sync helper not found: $SyncScript"
        Wait-ForUser
        return
    }

    if (-not (Ensure-BranchCheckedOut -BranchName $BranchName)) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local $BranchName with origin/$BranchName..." -ForegroundColor Cyan
    Write-Host "    If your tree is dirty, a local checkpoint commit will be created before the pull." -ForegroundColor Cyan
    Write-Host "    Post-sync health checks will run automatically." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $BranchName -AutoCheckpoint

    if ($LASTEXITCODE -eq 0) {
        if ($BranchName -eq 'staging') {
            Write-Host "`n[+] You are now on 'staging' and up to date with origin/staging." -ForegroundColor Green
        } elseif ($BranchName -eq 'main') {
            Write-Host "`n[+] You are now on 'main' and up to date with origin/main." -ForegroundColor Green
        } else {
            Write-Host "`n[+] Safe sync workflow completed for '$BranchName'." -ForegroundColor Green
        }
    } else {
        Write-Warning "Safe sync workflow stopped. Review the output above for conflicts or validation errors."
    }

    Wait-ForUser
}

function Invoke-FeatureBranchWorkflow {
    if (-not (Ensure-BranchCheckedOut -BranchName 'main')) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local main before branching..." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch main -AutoCheckpoint
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not sync local main. Feature branch creation stopped."
        Wait-ForUser
        return
    }

    $featureName = Read-Host "Enter the feature branch name (example: notable-parse)"
    if ([string]::IsNullOrWhiteSpace($featureName)) {
        Write-Warning "Feature branch creation cancelled."
        Wait-ForUser
        return
    }

    $featureBranch = $featureName.Trim()
    if ($featureBranch -notmatch '^feature/') {
        $featureBranch = "feature/$featureBranch"
    }

    if (Test-LocalBranchExists -BranchName $featureBranch) {
        Write-Host "`n[+] Switching to existing local branch '$featureBranch'..." -ForegroundColor Cyan
        Invoke-GitCommand -Args @('checkout', $featureBranch) | Out-Null
    } elseif (Test-RemoteBranchExists -BranchName $featureBranch) {
        Write-Host "`n[+] Tracking existing remote branch '$featureBranch'..." -ForegroundColor Cyan
        Invoke-GitCommand -Args @('checkout', '-b', $featureBranch, "origin/$featureBranch") | Out-Null
    } else {
        Write-Host "`n[+] Creating new feature branch '$featureBranch' from main..." -ForegroundColor Cyan
        Invoke-GitCommand -Args @('checkout', '-b', $featureBranch) | Out-Null
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Feature branch ready: $featureBranch" -ForegroundColor Green
    } else {
        Write-Warning "Feature branch workflow failed. Review the git output above."
    }

    Wait-ForUser
}

function Invoke-PushWorkflow {
    Write-Host "`n[+] Preparing to push changes..." -ForegroundColor Cyan
    $currentBranch = Get-CurrentBranch

    if ($currentBranch -eq 'main') {
        $confirm = Read-Host "You are on 'main'. Direct pushes should normally be promotion-only. Continue? (y/N)"
        if ($confirm -notin @('y', 'Y', 'yes', 'YES')) {
            Write-Host "Push cancelled." -ForegroundColor Yellow
            Wait-ForUser
            return
        }
    }

    $commitMsg = Read-Host "Enter a short description of your changes (or press Enter for 'Update SOC Platform files')"
    if ([string]::IsNullOrWhiteSpace($commitMsg)) {
        $commitMsg = 'Update SOC Platform files'
    }

    & powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch $currentBranch -All -Message $commitMsg
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Successfully pushed '$currentBranch'." -ForegroundColor Green
    } else {
        Write-Warning "Push workflow failed. Review the git output above."
    }

    Wait-ForUser
}

function Invoke-MergeToStagingWorkflow {
    $currentBranch = Get-CurrentBranch
    $sourceBranch = Read-Host "Feature branch to merge into staging (press Enter for '$currentBranch')"
    if ([string]::IsNullOrWhiteSpace($sourceBranch)) {
        $sourceBranch = $currentBranch
    }

    if ($sourceBranch -eq 'staging' -or $sourceBranch -eq 'main') {
        Write-Warning "Choose a feature branch as the merge source."
        Wait-ForUser
        return
    }

    if (-not (Ensure-BranchCheckedOut -BranchName 'staging')) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local staging before merge..." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch staging -AutoCheckpoint
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not sync staging. Merge stopped."
        Wait-ForUser
        return
    }

    $mergeMessage = Read-Host "Merge commit message (press Enter for 'Stage $sourceBranch')"
    if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
        $mergeMessage = "Stage $sourceBranch"
    }

    & powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch staging -MergeFrom $sourceBranch -Message $mergeMessage
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] '$sourceBranch' merged into staging and pushed." -ForegroundColor Green
    } else {
        Write-Warning "Merge to staging failed. Review the git output above."
    }

    Wait-ForUser
}

function Invoke-PromoteStagingWorkflow {
    $confirm = Read-Host "Promote staging into main? This should be the explicit final step. (y/N)"
    if ($confirm -notin @('y', 'Y', 'yes', 'YES')) {
        Write-Host "Promotion cancelled." -ForegroundColor Yellow
        Wait-ForUser
        return
    }

    if (-not (Ensure-BranchCheckedOut -BranchName 'main')) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local main before promotion..." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch main -AutoCheckpoint
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not sync main. Promotion stopped."
        Wait-ForUser
        return
    }

    $mergeMessage = Read-Host "Merge commit message (press Enter for 'Promote staging into main')"
    if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
        $mergeMessage = 'Promote staging into main'
    }

    & powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch main -MergeFrom staging -Message $mergeMessage
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] staging promoted into main and pushed." -ForegroundColor Green
    } else {
        Write-Warning "Promotion failed. Review the git output above."
    }

    Wait-ForUser
}

# --- Main Menu Loop ---
while ($true) {
    Write-Host "`n=================================" -ForegroundColor Cyan
    Write-Host "    SOC GIT WORKFLOW MENU        " -ForegroundColor Cyan
    Write-Host "=================================" -ForegroundColor Cyan
    Write-Host "Flow: main -> feature/* -> staging -> main" -ForegroundColor DarkCyan
    Write-Host "main    = stable baseline" -ForegroundColor DarkGray
    Write-Host "feature = active work" -ForegroundColor DarkGray
    Write-Host "staging = integration checkpoint" -ForegroundColor DarkGray
    Write-Host "---------------------------------" -ForegroundColor DarkCyan

    Show-Status

<<<<<<< HEAD
    Write-Host "1. Pull main             (switch to main and get latest)"
    Write-Host "2. Start feature work    (create or switch feature branch)"
    Write-Host "3. Push my branch        (save and share current work)"
    Write-Host "4. Pull staging          (switch to staging and get latest)"
=======
    Write-Host "1. Update main           (get latest stable baseline)"
    Write-Host "2. Start feature work    (create or switch feature branch)"
    Write-Host "3. Push my branch        (save and share current work)"
    Write-Host "4. Update staging        (get latest shared checkpoint)"
>>>>>>> staging
    Write-Host "5. Stage a feature       (move feature into staging)"
    Write-Host "6. Ship staging to main  (final promotion step)"
    Write-Host "7. View detailed git status"
    Write-Host "8. Exit"
    Write-Host "=================================" -ForegroundColor Cyan

    $choice = Read-Host "Select an option (1-8)"

    switch ($choice) {
        '1' { Invoke-SafeSyncWorkflow -BranchName 'main' }
        '2' { Invoke-FeatureBranchWorkflow }
        '3' { Invoke-PushWorkflow }
        '4' { Invoke-SafeSyncWorkflow -BranchName 'staging' }
        '5' { Invoke-MergeToStagingWorkflow }
        '6' { Invoke-PromoteStagingWorkflow }
        '7' { Show-DetailedStatus }
        '8' { exit }
        default { Write-Warning "Invalid selection. Please choose 1-8." }
    }
}
