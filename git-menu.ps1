Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $RepoRoot 'scripts\sync_upstream_safe.ps1'
$PushScript = Join-Path $RepoRoot 'git-sync.ps1'

# Change this if the team renames the one combined demo branch later.
$ConsolidationBranch = 'staging'

# Add noisy patterns here if you want them hidden from the active branch views.
$HiddenBranchPatterns = @(
    # 'agent-lewis/tester-*'
)

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Args
    )

    & git @Args
    return $LASTEXITCODE
}

function Wait-ForUser {
    Read-Host 'Press Enter to return' | Out-Null
}

function Get-CurrentBranch {
    return (git branch --show-current).Trim()
}

function Get-BranchCategory {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    if ($BranchName -eq 'main') {
        return 'main'
    }

    if ($BranchName -eq $ConsolidationBranch) {
        return 'demo'
    }

    return 'work'
}

function Get-WorkGuidance {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    if ($BranchName -eq 'main') {
        return 'NO - switch to your work branch first.'
    }

    if ($BranchName -eq $ConsolidationBranch) {
        return 'NO - only use this to combine branches for the demo.'
    }

    return 'YES - this is a normal work/staging branch.'
}

function Get-NextStepHint {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    if ($BranchName -eq 'main') {
        return 'Use 2 to open the branch where you should work.'
    }

    if ($BranchName -eq $ConsolidationBranch) {
        return 'If you want to keep coding, use 2. If you are combining work, use 5.'
    }

    return 'Code here, then use 3 to save and share it.'
}

function Get-ModeLabel {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    if ($BranchName -eq 'main') {
        return 'START MODE'
    }

    if ($BranchName -eq $ConsolidationBranch) {
        return 'DEMO COMBINE MODE'
    }

    return 'CODING MODE'
}

function Test-BranchHidden {
    param(
        [Parameter(Mandatory = $true)][string]$BranchName
    )

    foreach ($pattern in $HiddenBranchPatterns) {
        if ($BranchName -like $pattern) {
            return $true
        }
    }

    return $false
}

function Get-LocalBranchNames {
    $branchLines = @(git for-each-ref --format='%(refname:short)' refs/heads)
    return @($branchLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Get-RemoteBranchNames {
    $branchLines = @(git for-each-ref --format='%(refname:short)' refs/remotes/origin)
    return @(
        $branchLines |
        Where-Object {
            -not [string]::IsNullOrWhiteSpace($_) -and
            $_ -ne 'origin' -and
            $_ -ne 'origin/HEAD'
        } |
        ForEach-Object { $_ -replace '^origin/', '' }
    )
}

function Get-LocalWorkBranches {
    param(
        [switch]$IncludeHidden
    )

    $branches = @(
        Get-LocalBranchNames |
        Where-Object { (Get-BranchCategory -BranchName $_) -eq 'work' }
    )

    if (-not $IncludeHidden) {
        $branches = @($branches | Where-Object { -not (Test-BranchHidden -BranchName $_) })
    }

    return @($branches | Sort-Object)
}

function Get-RemoteWorkBranches {
    param(
        [switch]$IncludeHidden
    )

    $branches = @(
        Get-RemoteBranchNames |
        Where-Object { (Get-BranchCategory -BranchName $_) -eq 'work' }
    )

    if (-not $IncludeHidden) {
        $branches = @($branches | Where-Object { -not (Test-BranchHidden -BranchName $_) })
    }

    return @($branches | Sort-Object)
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
            Write-Warning "Could not switch to '$BranchName' because local changes would be overwritten. Commit or stash your changes first."
            return $false
        }

        Write-Warning "Could not switch to local branch '$BranchName'. Review the git output above."
        return $false
    }

    if (Test-RemoteBranchExists -BranchName $BranchName) {
        if ((Invoke-GitCommand -Args @('checkout', '-b', $BranchName, "origin/$BranchName")) -eq 0) {
            return $true
        }

        if (Test-WorkingTreeDirty) {
            Write-Warning "Could not create local '$BranchName' from origin/$BranchName because local changes would be overwritten. Commit or stash your changes first."
            return $false
        }

        Write-Warning "origin/$BranchName exists, but local checkout failed. Review the git output above."
        return $false
    }

    Write-Warning "Branch '$BranchName' does not exist locally or on origin."
    return $false
}

function Show-Status {
    $currentBranch = Get-CurrentBranch
    $changeCount = @(git status --porcelain).Count
    $nextStep = Get-NextStepHint -BranchName $currentBranch

    Write-Host (("Branch: {0} | Mode: {1}" -f $currentBranch, (Get-ModeLabel -BranchName $currentBranch))) -ForegroundColor Yellow
    Write-Host (("Work here: {0}" -f (Get-WorkGuidance -BranchName $currentBranch))) -ForegroundColor Yellow
    Write-Host (("Changes: {0} | Next: {1}" -f $changeCount, $nextStep)) -ForegroundColor Yellow
}

function Show-DetailedStatus {
    Write-Host "`n--- Git Status ---" -ForegroundColor Yellow
    git status -sb
    Write-Host "`n--- Remotes ---" -ForegroundColor Yellow
    git remote -v
    Write-Host "" 
    Wait-ForUser
}

function Show-BranchGuide {
    $currentBranch = Get-CurrentBranch
    Write-Host "`n--- Branch Guide ---" -ForegroundColor Yellow
    Write-Host (("You are on: {0}" -f $currentBranch)) -ForegroundColor Yellow
    Write-Host 'main     = never do normal coding here' -ForegroundColor DarkGray
    Write-Host 'work/staging = branches where people actively code' -ForegroundColor DarkGray
    Write-Host (("demo branch = the ONE combined branch for the meeting ({0})" -f $ConsolidationBranch)) -ForegroundColor DarkGray
    Write-Host "" 
    Write-Host 'Flow: main -> work/staging branches -> one demo branch -> main' -ForegroundColor Yellow
    Write-Host "" 
    Wait-ForUser
}

function Show-ActiveBranches {
    $currentBranch = Get-CurrentBranch
    $localBranches = Get-LocalBranchNames
    $localWorkBranches = @(Get-LocalWorkBranches)
    $remoteWorkBranches = @(Get-RemoteWorkBranches)

    Write-Host "`n--- Branches You Can Use ---" -ForegroundColor Yellow
    Write-Host (("Current: {0}" -f $currentBranch)) -ForegroundColor Yellow
    Write-Host (("Safe place to code: {0}" -f (Get-WorkGuidance -BranchName $currentBranch))) -ForegroundColor Yellow
    Write-Host "" 
    Write-Host 'Your normal work/staging branches:' -ForegroundColor Yellow
    if ($localWorkBranches.Count -eq 0) {
        Write-Host '  (none yet - use main menu option 2)' -ForegroundColor DarkGray
    } else {
        foreach ($branch in $localWorkBranches) {
            $marker = if ($branch -eq $currentBranch) { '*' } else { '-' }
            Write-Host (("  {0} {1}" -f $marker, $branch)) -ForegroundColor DarkGray
        }
    }

    Write-Host "" 
    Write-Host (("Configured demo branch: {0}" -f $ConsolidationBranch)) -ForegroundColor Yellow
    Write-Host '  This is the ONE branch used to combine selected work for the meeting.' -ForegroundColor DarkGray
    Write-Host '  Use main menu option 4 to open it.' -ForegroundColor DarkGray

    Write-Host "" 
    Write-Host 'Other shared branches you may hear about:' -ForegroundColor Yellow
    if ($remoteWorkBranches.Count -eq 0) {
        Write-Host '  (none visible)' -ForegroundColor DarkGray
    } else {
        foreach ($branch in $remoteWorkBranches) {
            $location = if ($localBranches -contains $branch) { 'also on this machine' } else { 'shared remote only' }
            Write-Host (("  - {0} ({1})" -f $branch, $location)) -ForegroundColor DarkGray
        }
    }

    Write-Host "" 
    Write-Host "What to do with this screen:" -ForegroundColor Yellow
    Write-Host "  If you want to code, pick one branch under 'Your normal work/staging branches'." -ForegroundColor DarkGray
    Write-Host "  Do not sit in the configured demo branch unless you are combining work for the meeting." -ForegroundColor DarkGray
    Write-Host "" 
    Wait-ForUser
}

function Show-CleanupCandidates {
    $currentBranch = Get-CurrentBranch
    $mergedIntoMain = @(
        git for-each-ref --format='%(refname:short)' --merged refs/heads/main refs/heads |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    $localCleanupCandidates = @(
        $mergedIntoMain |
        Where-Object {
            $_ -ne 'main' -and
            $_ -ne $ConsolidationBranch -and
            $_ -ne $currentBranch
        } |
        Sort-Object
    )

    Write-Host "`n--- Cleanup Ideas ---" -ForegroundColor Yellow
    Write-Host 'These local branches are already merged into main.' -ForegroundColor Yellow
    if ($localCleanupCandidates.Count -eq 0) {
        Write-Host '  No local cleanup candidates found.' -ForegroundColor DarkGray
    } else {
        foreach ($branch in $localCleanupCandidates) {
            Write-Host (("  - {0}" -f $branch)) -ForegroundColor DarkGray
        }
    }

    Write-Host "" 
    Wait-ForUser
}

function Invoke-PruneRemoteRefs {
    Write-Host "`n[+] Pruning stale remote refs from origin..." -ForegroundColor Cyan
    Invoke-GitCommand -Args @('fetch', '--prune', 'origin') | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Remote refs pruned successfully." -ForegroundColor Green
    } else {
        Write-Warning 'Prune failed. Review the git output above.'
    }

    Wait-ForUser
}

function Show-ToolsMenu {
    while ($true) {
        Write-Host "`n--- BRANCH TOOLS ---" -ForegroundColor Cyan
        Write-Host '1. What do these branches mean?' -ForegroundColor Yellow
        Write-Host '2. Which branch should I work on?' -ForegroundColor Yellow
        Write-Host '3. Show raw git status' -ForegroundColor Yellow
        Write-Host '4. Which branches can I clean up?' -ForegroundColor Yellow
        Write-Host '5. Remove stale remote branch refs' -ForegroundColor Yellow
        Write-Host '6. Back' -ForegroundColor Yellow

        $toolChoice = Read-Host 'Select an option (1-6)'

        switch ($toolChoice) {
            '1' { Show-BranchGuide }
            '2' { Show-ActiveBranches }
            '3' { Show-DetailedStatus }
            '4' { Show-CleanupCandidates }
            '5' { Invoke-PruneRemoteRefs }
            '6' { return }
            default { Write-Warning 'Invalid selection. Please choose 1-6.' }
        }
    }
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
    Write-Host '    If your tree is dirty, a local checkpoint commit will be created before the pull.' -ForegroundColor Cyan
    Write-Host '    Post-sync health checks will run automatically.' -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $BranchName -AutoCheckpoint

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Safe sync completed for '$BranchName'." -ForegroundColor Green
    } else {
        Write-Warning 'Safe sync stopped. Review the output above for conflicts or validation errors.'
    }

    Wait-ForUser
}

function Invoke-FeatureBranchWorkflow {
    $currentBranch = Get-CurrentBranch

    if ($currentBranch -eq $ConsolidationBranch -and (Test-WorkingTreeDirty)) {
        Write-Warning "You are on the demo branch and have unsaved changes here. Save them with option 3, or stash them manually, before leaving demo mode."
        Wait-ForUser
        return
    }

    if (-not (Ensure-BranchCheckedOut -BranchName 'main')) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local main before branching..." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch main -AutoCheckpoint
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Could not sync local main. Work branch creation stopped.'
        Wait-ForUser
        return
    }

    $branchName = Read-Host 'Enter the branch name you want to work on'
    if ([string]::IsNullOrWhiteSpace($branchName)) {
        Write-Warning 'Work branch creation cancelled.'
        Wait-ForUser
        return
    }

    $branchName = $branchName.Trim()

    if (Test-LocalBranchExists -BranchName $branchName) {
        Write-Host "`n[+] Switching to local branch '$branchName'..." -ForegroundColor Cyan
        Invoke-GitCommand -Args @('checkout', $branchName) | Out-Null
    } elseif (Test-RemoteBranchExists -BranchName $branchName) {
        Write-Host "`n[+] Tracking remote branch '$branchName'..." -ForegroundColor Cyan
        Invoke-GitCommand -Args @('checkout', '-b', $branchName, "origin/$branchName") | Out-Null
    } else {
        Write-Host "`n[+] Creating new work branch '$branchName' from main..." -ForegroundColor Cyan
        Invoke-GitCommand -Args @('checkout', '-b', $branchName) | Out-Null
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Work branch ready: $branchName" -ForegroundColor Green
    } else {
        Write-Warning 'Work branch workflow failed. Review the git output above.'
    }

    Wait-ForUser
}

function Invoke-PushWorkflow {
    Write-Host "`n[+] Preparing to push changes..." -ForegroundColor Cyan
    $currentBranch = Get-CurrentBranch

    if ($currentBranch -eq 'main') {
        Write-Warning "Direct commits and pushes to 'main' are blocked. Move to a work branch first."
        Wait-ForUser
        return
    }

    $commitMsg = Read-Host "Enter a short description of your changes (or press Enter for 'Update SOC Platform files')"
    if ([string]::IsNullOrWhiteSpace($commitMsg)) {
        $commitMsg = 'Update SOC Platform files'
    }

    & powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch $currentBranch -All -Message $commitMsg
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] Successfully pushed '$currentBranch'." -ForegroundColor Green
    } else {
        Write-Warning 'Push workflow failed. Review the git output above.'
    }

    Wait-ForUser
}

function Invoke-MergeToConsolidationWorkflow {
    $currentBranch = Get-CurrentBranch
    $sourceBranch = Read-Host "Which work branch should go into $ConsolidationBranch? (press Enter for '$currentBranch')"
    if ([string]::IsNullOrWhiteSpace($sourceBranch)) {
        $sourceBranch = $currentBranch
    }

    if ($sourceBranch -eq $ConsolidationBranch -or $sourceBranch -eq 'main') {
        Write-Warning 'Choose a work branch as the merge source.'
        Wait-ForUser
        return
    }

    if (-not (Ensure-BranchCheckedOut -BranchName $ConsolidationBranch)) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local $ConsolidationBranch before merge..." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $ConsolidationBranch -AutoCheckpoint
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Could not sync the demo branch. Merge stopped.'
        Wait-ForUser
        return
    }

    $mergeMessage = Read-Host "Merge commit message (press Enter for 'Merge $sourceBranch into $ConsolidationBranch')"
    if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
        $mergeMessage = "Merge $sourceBranch into $ConsolidationBranch"
    }

    & powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch $ConsolidationBranch -MergeFrom $sourceBranch -Message $mergeMessage
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] '$sourceBranch' merged into $ConsolidationBranch and pushed." -ForegroundColor Green
    } else {
        Write-Warning 'Merge into the demo branch failed. Review the git output above.'
    }

    Wait-ForUser
}

function Invoke-PromoteConsolidationWorkflow {
    $confirm = Read-Host "Merge $ConsolidationBranch into main now? This should only happen after team sign-off. (y/N)"
    if ($confirm -notin @('y', 'Y', 'yes', 'YES')) {
        Write-Host 'Final merge cancelled.' -ForegroundColor Yellow
        Wait-ForUser
        return
    }

    if (-not (Ensure-BranchCheckedOut -BranchName 'main')) {
        Wait-ForUser
        return
    }

    Write-Host "`n[+] Syncing local main before final merge..." -ForegroundColor Cyan
    powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch main -AutoCheckpoint
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Could not sync main. Final merge stopped.'
        Wait-ForUser
        return
    }

    $mergeMessage = Read-Host "Merge commit message (press Enter for 'Merge $ConsolidationBranch into main')"
    if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
        $mergeMessage = "Merge $ConsolidationBranch into main"
    }

    & powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch main -MergeFrom $ConsolidationBranch -Message $mergeMessage
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[+] $ConsolidationBranch merged into main and pushed." -ForegroundColor Green
    } else {
        Write-Warning 'Final merge into main failed. Review the git output above.'
    }

    Wait-ForUser
}

while ($true) {
    $currentBranch = Get-CurrentBranch
    $branchActionLabel = if ($currentBranch -eq $ConsolidationBranch) { 'Leave demo / go work' } else { 'Go to my branch' }
    $demoActionLabel = if ($currentBranch -eq $ConsolidationBranch) { 'Refresh demo branch' } else { 'Open demo branch' }

    Write-Host "`n=================================" -ForegroundColor Cyan
    Write-Host '    SOC GIT WORKFLOW MENU        ' -ForegroundColor Cyan
    Write-Host '=================================' -ForegroundColor Cyan
    Write-Host (("Flow: main -> work/staging branches -> {0} -> main" -f $ConsolidationBranch)) -ForegroundColor DarkCyan
    Show-Status
    Write-Host (("1. Refresh main      2. {0,-19} 3. Save my work" -f $branchActionLabel)) -ForegroundColor Yellow
    Write-Host (("4. {0,-18} 5. Add work to demo  6. Final merge" -f $demoActionLabel)) -ForegroundColor Yellow
    Write-Host '7. Branch tools      8. Exit' -ForegroundColor Yellow
    Write-Host '=================================' -ForegroundColor Cyan

    $choice = Read-Host 'Select an option (1-8)'

    switch ($choice) {
        '1' { Invoke-SafeSyncWorkflow -BranchName 'main' }
        '2' { Invoke-FeatureBranchWorkflow }
        '3' { Invoke-PushWorkflow }
        '4' { Invoke-SafeSyncWorkflow -BranchName $ConsolidationBranch }
        '5' { Invoke-MergeToConsolidationWorkflow }
        '6' { Invoke-PromoteConsolidationWorkflow }
        '7' { Show-ToolsMenu }
        '8' { exit }
        default { Write-Warning 'Invalid selection. Please choose 1-8.' }
    }
}
