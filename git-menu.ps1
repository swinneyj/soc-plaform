Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $RepoRoot 'scripts\sync_upstream_safe.ps1'
$PushScript = Join-Path $RepoRoot 'git-sync.ps1'

# Update this if the team changes the one combined demo branch name.
$DemoBranch = 'staging'

# Add noisy branch patterns here if you want to hide them from branch-helper screens.
$HiddenBranchPatterns = @(
	# 'agent-lewis/tester-*'
)

$MenuHeaderColor = 'Cyan'
$MenuSubheaderColor = 'DarkCyan'
$MenuTextColor = 'Gray'
$MenuMutedColor = 'DarkGray'

function Initialize-MenuTheme {
	try {
		if ($host.UI.RawUI) {
			$host.UI.RawUI.BackgroundColor = 'Black'
			$host.UI.RawUI.ForegroundColor = $MenuTextColor
			Clear-Host
		}
	} catch {
		# Some hosts do not allow RawUI palette changes; explicit Write-Host colors still apply.
	}
}

function Invoke-GitCommand {
	param(
		[Parameter(Mandatory = $true)][string[]]$Args
	)

	& git @Args | Out-Host
	return [int]$LASTEXITCODE
}

function Wait-ForUser {
	Read-Host 'Press Enter to return' | Out-Null
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

function Get-BranchCategory {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'main'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'demo'
	}

	return 'work'
}

function Get-ModeLabel {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'MAIN / START ONLY'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'STAGING / COMBINE ONLY'
	}

	return 'WORK BRANCH / OK TO CODE'
}

function Get-WorkGuidance {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'NO - switch to a work branch first.'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'NO - use this only to combine branches for the demo.'
	}

	return 'YES - this is a normal work branch.'
}

function Get-NextStepHint {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'Pick 1 if you want to start or continue coding.'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'Pick 1 to leave demo mode and code, or 4 to combine work here.'
	}

	return 'Code here, then pick 2 when you want to save and share it.'
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

function Get-MergedLocalWorkBranches {
	$currentBranch = Get-CurrentBranch
	$mergedBranches = @(
		git for-each-ref --format='%(refname:short)' --merged refs/heads/main refs/heads |
		Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
	)

	return @(
		$mergedBranches |
		Where-Object {
			(Get-BranchCategory -BranchName $_) -eq 'work' -and
			$_ -ne $currentBranch -and
			-not (Test-BranchHidden -BranchName $_)
		} |
		Sort-Object
	)
}

function Get-RecommendedWorkBranch {
	$currentBranch = Get-CurrentBranch
	$localWorkBranches = @(Get-LocalWorkBranches)

	if ((Get-BranchCategory -BranchName $currentBranch) -eq 'work') {
		return $currentBranch
	}

	if ($localWorkBranches.Count -eq 0) {
		return $null
	}

	$branchData = @()
	foreach ($branch in $localWorkBranches) {
		$timestamp = git log -1 --format=%ct $branch 2>$null
		if ([string]::IsNullOrWhiteSpace($timestamp)) {
			$timestamp = '0'
		}

		$branchData += [pscustomobject]@{
			Name = $branch
			Timestamp = [int64]$timestamp
		}
	}

	return ($branchData | Sort-Object Timestamp -Descending | Select-Object -First 1).Name
}

function Select-BranchFromList {
	param(
		[Parameter(Mandatory = $true)][string]$Title,
		[Parameter(Mandatory = $true)][object[]]$Options,
		[string]$CancelLabel = 'Cancel'
	)

	Write-Host "`n--- $Title ---" -ForegroundColor $MenuSubheaderColor

	if ($Options.Count -eq 0) {
		Write-Host 'No branch options are available.' -ForegroundColor $MenuMutedColor
		return $null
	}

	for ($index = 0; $index -lt $Options.Count; $index++) {
		$option = $Options[$index]
		$details = if ([string]::IsNullOrWhiteSpace($option.Description)) { '' } else { " ($($option.Description))" }
		Write-Host (("{0}. {1}{2}" -f ($index + 1), $option.Name, $details)) -ForegroundColor $MenuTextColor
	}

	Write-Host (("{0}. {1}" -f ($Options.Count + 1), $CancelLabel)) -ForegroundColor $MenuTextColor

	$selection = Read-Host (("Select an option (1-{0})" -f ($Options.Count + 1)))
	[int]$selectedNumber = 0
	if (-not [int]::TryParse($selection, [ref]$selectedNumber)) {
		Write-Warning 'Please enter a number from the list.'
		return $null
	}

	if ($selectedNumber -eq ($Options.Count + 1)) {
		return $null
	}

	if ($selectedNumber -lt 1 -or $selectedNumber -gt $Options.Count) {
		Write-Warning 'Please select a valid branch option.'
		return $null
	}

	return $Options[$selectedNumber - 1].Name
}

function Select-ExistingWorkBranch {
	param(
		[string]$RecommendedBranch,
		[string]$Title = 'Choose a work branch'
	)

	$localWorkBranches = @(Get-LocalWorkBranches)
	$remoteOnlyBranches = @(
		Get-RemoteWorkBranches |
		Where-Object { -not (Test-LocalBranchExists -BranchName $_) }
	)

	$options = @()
	foreach ($branch in $localWorkBranches) {
		$description = if ($branch -eq $RecommendedBranch) { 'recommended, local' } else { 'local' }
		$options += [pscustomobject]@{ Name = $branch; Description = $description }
	}

	foreach ($branch in $remoteOnlyBranches) {
		$description = if ($branch -eq $RecommendedBranch) { 'recommended, remote only' } else { 'remote only' }
		$options += [pscustomobject]@{ Name = $branch; Description = $description }
	}

	return Select-BranchFromList -Title $Title -Options $options
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

function Switch-ToBranch {
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

	Write-Host (("Branch: {0} | Mode: {1}" -f $currentBranch, (Get-ModeLabel -BranchName $currentBranch))) -ForegroundColor $MenuTextColor
	Write-Host (("Work here: {0}" -f (Get-WorkGuidance -BranchName $currentBranch))) -ForegroundColor $MenuTextColor
	Write-Host (("Changes: {0} | Next: {1}" -f $changeCount, $nextStep)) -ForegroundColor $MenuTextColor
	Write-Host 'Need help choosing a branch? Pick 6, then 2.' -ForegroundColor $MenuMutedColor
}

function Show-DetailedStatus {
	Write-Host "`n--- Git Status ---" -ForegroundColor $MenuSubheaderColor
	git status -sb
	Write-Host "`n--- Remotes ---" -ForegroundColor $MenuSubheaderColor
	git remote -v
	Write-Host ""
	Wait-ForUser
}

function Show-BranchGuide {
	$currentBranch = Get-CurrentBranch
	Write-Host "`n--- Branch Guide ---" -ForegroundColor $MenuSubheaderColor
	Write-Host (("Current branch: {0}" -f $currentBranch)) -ForegroundColor $MenuTextColor
	Write-Host 'main           = safe starting point only' -ForegroundColor $MenuMutedColor
	Write-Host 'work branch    = where you normally code' -ForegroundColor $MenuMutedColor
	Write-Host (("staging branch = the one combined meeting branch ({0})" -f $DemoBranch)) -ForegroundColor $MenuMutedColor
	Write-Host ""
	Write-Host 'Normal flow: main -> your work branch -> staging -> main' -ForegroundColor $MenuTextColor
	Write-Host ""
	Wait-ForUser
}

function Show-BranchChooserHelp {
	$currentBranch = Get-CurrentBranch
	$localWorkBranches = @(Get-LocalWorkBranches)
	$remoteWorkBranches = @(Get-RemoteWorkBranches)
	$mergedLocalWorkBranches = @(Get-MergedLocalWorkBranches)
	$recommendedBranch = Get-RecommendedWorkBranch
	$activeLocalWorkBranches = @($localWorkBranches | Where-Object { $mergedLocalWorkBranches -notcontains $_ })
	$remoteOnlyWorkBranches = @($remoteWorkBranches | Where-Object { -not (Test-LocalBranchExists -BranchName $_) })

	Write-Host "`n--- Which Branch Should I Use? ---" -ForegroundColor $MenuSubheaderColor
	Write-Host (("Current: {0}" -f $currentBranch)) -ForegroundColor $MenuTextColor
	Write-Host (("Can you code here? {0}" -f (Get-WorkGuidance -BranchName $currentBranch))) -ForegroundColor $MenuTextColor
	Write-Host ""
	Write-Host 'Best answer right now:' -ForegroundColor $MenuTextColor
	if ($currentBranch -eq 'main') {
		Write-Host '  You should move off main before coding.' -ForegroundColor $MenuMutedColor
		Write-Host '  Use main menu option 1 to open or create your work branch.' -ForegroundColor $MenuMutedColor
	} elseif ($currentBranch -eq $DemoBranch) {
		Write-Host '  You are in staging mode, not coding mode.' -ForegroundColor $MenuMutedColor
		Write-Host '  Use main menu option 1 if you want to go back to a normal work branch.' -ForegroundColor $MenuMutedColor
	} else {
		Write-Host (("  Stay on '{0}' if this is the branch you are actively coding in." -f $currentBranch)) -ForegroundColor $MenuMutedColor
		Write-Host '  Use main menu option 2 when you want to save and share your changes.' -ForegroundColor $MenuMutedColor
	}

	Write-Host ""
	Write-Host 'Active local work branches:' -ForegroundColor $MenuTextColor
	if ($activeLocalWorkBranches.Count -eq 0) {
		Write-Host '  (none yet - use main menu option 1 to create one)' -ForegroundColor $MenuMutedColor
	} else {
		if ($currentBranch -eq 'main' -or $currentBranch -eq $DemoBranch) {
			Write-Host (("  Recommended now: {0}" -f $recommendedBranch)) -ForegroundColor $MenuHeaderColor
			Write-Host '  Use main menu option 1, then choose that branch name.' -ForegroundColor $MenuMutedColor
			Write-Host ""
		}
		foreach ($branch in $activeLocalWorkBranches) {
			$marker = if ($branch -eq $currentBranch) { '*' } else { '-' }
			$suffix = if ($branch -eq $currentBranch) { ' <- current branch' } elseif ($branch -eq $recommendedBranch) { ' <- recommended' } else { '' }
			Write-Host (("  {0} {1}{2}" -f $marker, $branch, $suffix)) -ForegroundColor $MenuMutedColor
		}
	}

	Write-Host ""
	Write-Host (("Staging branch: {0}" -f $DemoBranch)) -ForegroundColor $MenuTextColor
	Write-Host '  Ignore this for normal coding. Use it only when combining work for the meeting.' -ForegroundColor $MenuMutedColor

	Write-Host ""
	Write-Host 'Already merged into main (usually safe to ignore or clean up later):' -ForegroundColor $MenuTextColor
	if ($mergedLocalWorkBranches.Count -eq 0) {
		Write-Host '  (none)' -ForegroundColor $MenuMutedColor
	} else {
		foreach ($branch in $mergedLocalWorkBranches) {
			Write-Host (("  - {0}" -f $branch)) -ForegroundColor $MenuMutedColor
		}
	}

	Write-Host ""
	Write-Host 'Remote-only / probably old shared branches (ignore unless you were told to use one):' -ForegroundColor $MenuTextColor
	if ($remoteOnlyWorkBranches.Count -eq 0) {
		Write-Host '  (none visible)' -ForegroundColor $MenuMutedColor
	} else {
		foreach ($branch in $remoteOnlyWorkBranches) {
			Write-Host (("  - {0}" -f $branch)) -ForegroundColor $MenuMutedColor
		}
	}

	Write-Host ""
	Write-Host 'What to do next:' -ForegroundColor $MenuTextColor
	Write-Host '  If you want to start or continue coding, use one branch from the active local work list.' -ForegroundColor $MenuMutedColor
	Write-Host '  If you are ready to push a work branch into staging, use main menu option 4.' -ForegroundColor $MenuMutedColor
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
			$_ -ne $DemoBranch -and
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
		Write-Host "`n--- BRANCH TOOLS ---" -ForegroundColor $MenuHeaderColor
		Write-Host '1. What do these branches mean?' -ForegroundColor $MenuTextColor
		Write-Host '2. Help me choose a branch' -ForegroundColor $MenuTextColor
		Write-Host '3. Show raw git status' -ForegroundColor $MenuTextColor
		Write-Host '4. Which branches can I clean up?' -ForegroundColor $MenuTextColor
		Write-Host '5. Remove stale remote branch refs' -ForegroundColor $MenuTextColor
		Write-Host '6. Back' -ForegroundColor $MenuTextColor

		$toolChoice = Read-Host 'Select an option (1-6)'

		switch ($toolChoice) {
			'1' { Show-BranchGuide }
			'2' { Show-BranchChooserHelp }
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

	if (-not (Switch-ToBranch -BranchName $BranchName)) {
		Wait-ForUser
		return
	}

	Write-Host "`n[+] Syncing local $BranchName with origin/$BranchName..." -ForegroundColor Cyan
	Write-Host '    If your tree is dirty, a local checkpoint commit will be created before the pull.' -ForegroundColor Cyan
	Write-Host '    Post-sync health checks will run automatically.' -ForegroundColor Cyan
	powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $BranchName -AutoCheckpoint

	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] Safe sync completed for '{0}'." -f $BranchName)) -ForegroundColor Green
	} else {
		Write-Warning 'Safe sync stopped. Review the output above for conflicts or validation errors.'
	}

	Wait-ForUser
}

function Invoke-StartCodingWorkflow {
	$currentBranch = Get-CurrentBranch
	$currentCategory = Get-BranchCategory -BranchName $currentBranch
	$workingTreeDirty = Test-WorkingTreeDirty

	if ($currentCategory -eq 'work' -and $workingTreeDirty) {
		Write-Host "`n--- Already In Coding Mode ---" -ForegroundColor Yellow
		Write-Host (("You are already on work branch '{0}' and you have unsaved changes here." -f $currentBranch)) -ForegroundColor Yellow
		Write-Host 'Keep coding on this branch, or use option 2 when you are ready to save and share it.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	if ($currentBranch -eq $DemoBranch -and $workingTreeDirty) {
		Write-Warning 'You are on the demo branch and have unsaved changes here. Save them with option 2, or stash them manually, before leaving demo mode.'
		Wait-ForUser
		return
	}

	if ($currentBranch -eq 'main' -and $workingTreeDirty) {
		Write-Host "`n--- Main Has Unsaved Changes ---" -ForegroundColor $MenuSubheaderColor
		Write-Host 'You already have uncommitted changes on main.' -ForegroundColor $MenuTextColor
		Write-Host 'Those changes should go onto a work branch before you continue.' -ForegroundColor $MenuTextColor
		Write-Host '1. Create a new work branch and keep these current changes' -ForegroundColor $MenuTextColor
		Write-Host '2. Move these current changes onto an existing work branch' -ForegroundColor $MenuTextColor
		Write-Host '3. Cancel' -ForegroundColor $MenuTextColor

		$dirtyMainChoice = Read-Host 'Select an option (1-3)'
		switch ($dirtyMainChoice) {
			'1' {
				$newBranchName = Read-Host 'Enter the name of the new work branch for these current changes'
				if ([string]::IsNullOrWhiteSpace($newBranchName)) {
					Write-Warning 'No branch name entered. Start-coding workflow cancelled.'
					Wait-ForUser
					return
				}

				$newBranchName = $newBranchName.Trim()
				if (Test-LocalBranchExists -BranchName $newBranchName) {
					Write-Warning "Branch '$newBranchName' already exists locally. Start-coding workflow cancelled."
					Wait-ForUser
					return
				}

				Write-Host (("`n[+] Creating new work branch '{0}' from your current changes..." -f $newBranchName)) -ForegroundColor Cyan
				Invoke-GitCommand -Args @('checkout', '-b', $newBranchName) | Out-Null

				if ($LASTEXITCODE -eq 0) {
					Write-Host (("`n[+] You are now on work branch '{0}' with your current changes." -f $newBranchName)) -ForegroundColor Green
				} else {
					Write-Warning 'Could not create the new work branch from your current changes. Review the git output above.'
				}

				Wait-ForUser
				return
			}
			'2' {
				$recommendedBranch = Get-RecommendedWorkBranch
				$targetBranch = Select-ExistingWorkBranch -RecommendedBranch $recommendedBranch -Title 'Choose the work branch to receive these current changes'
				if ([string]::IsNullOrWhiteSpace($targetBranch)) {
					Write-Warning 'No work branch selected. Start-coding workflow cancelled.'
					Wait-ForUser
					return
				}

				$stashMessage = "move-off-main-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss')
				Write-Host (("`n[+] Stashing current main changes so they can be moved onto '{0}'..." -f $targetBranch)) -ForegroundColor Cyan
				& git stash push -u -m $stashMessage | Out-Host
				if ($LASTEXITCODE -ne 0) {
					Write-Warning 'Could not stash the current main changes. Review the git output above.'
					Wait-ForUser
					return
				}

				if (-not (Switch-ToBranch -BranchName $targetBranch)) {
					Write-Warning 'Could not switch to the selected work branch. Attempting to restore your stashed changes on main.'
					if ((Get-CurrentBranch) -eq 'main') {
						& git stash pop | Out-Host
					}
					Wait-ForUser
					return
				}

				Write-Host (("`n[+] Applying those stashed changes onto work branch '{0}'..." -f $targetBranch)) -ForegroundColor Cyan
				& git stash pop | Out-Host
				if ($LASTEXITCODE -eq 0) {
					Write-Host (("`n[+] You are now on work branch '{0}' with your former main changes applied here." -f $targetBranch)) -ForegroundColor Green
				} else {
					Write-Warning 'The stash did not apply cleanly. Your changes may still be in the stash list; review the git output above.'
				}

				Wait-ForUser
				return
			}
			default {
				Write-Warning 'Start-coding workflow cancelled.'
				Wait-ForUser
				return
			}
		}
	}

	if (-not (Switch-ToBranch -BranchName 'main')) {
		Wait-ForUser
		return
	}

	Write-Host "`n[+] Syncing local main before opening a work branch..." -ForegroundColor Cyan
	powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch main -AutoCheckpoint
	if ($LASTEXITCODE -ne 0) {
		Write-Warning 'Could not sync local main. Start-coding workflow stopped.'
		Wait-ForUser
		return
	}

	$recommendedBranch = Get-RecommendedWorkBranch
	Write-Host "`n--- Start Or Continue Coding ---" -ForegroundColor $MenuSubheaderColor
	if ($recommendedBranch) {
		Write-Host (("1. Use recommended branch: {0}" -f $recommendedBranch)) -ForegroundColor $MenuTextColor
		Write-Host '2. Pick a different existing branch' -ForegroundColor $MenuTextColor
		Write-Host '3. Create a new branch' -ForegroundColor $MenuTextColor
		Write-Host '4. Cancel' -ForegroundColor $MenuTextColor
	} else {
		Write-Host '1. Pick an existing branch' -ForegroundColor $MenuTextColor
		Write-Host '2. Create a new branch' -ForegroundColor $MenuTextColor
		Write-Host '3. Cancel' -ForegroundColor $MenuTextColor
	}

	$workflowChoice = Read-Host 'Select an option'
	$branchName = $null

	if ($recommendedBranch) {
		switch ($workflowChoice) {
			'1' { $branchName = $recommendedBranch }
			'2' { $branchName = Select-ExistingWorkBranch -RecommendedBranch $recommendedBranch -Title 'Choose a different work branch' }
			'3' {
				$branchName = Read-Host 'Enter the name of the new work branch'
				if (-not [string]::IsNullOrWhiteSpace($branchName)) { $branchName = $branchName.Trim() }
			}
			default { }
		}
	} else {
		switch ($workflowChoice) {
			'1' { $branchName = Select-ExistingWorkBranch -Title 'Choose a work branch' }
			'2' {
				$branchName = Read-Host 'Enter the name of the new work branch'
				if (-not [string]::IsNullOrWhiteSpace($branchName)) { $branchName = $branchName.Trim() }
			}
			default { }
		}
	}

	if ([string]::IsNullOrWhiteSpace($branchName)) {
		Write-Warning 'Start-coding workflow cancelled.'
		Wait-ForUser
		return
	}

	if (Test-LocalBranchExists -BranchName $branchName) {
		Write-Host (("`n[+] Switching to local work branch '{0}'..." -f $branchName)) -ForegroundColor Cyan
		Invoke-GitCommand -Args @('checkout', $branchName) | Out-Null
	} elseif (Test-RemoteBranchExists -BranchName $branchName) {
		Write-Host (("`n[+] Tracking remote work branch '{0}'..." -f $branchName)) -ForegroundColor Cyan
		Invoke-GitCommand -Args @('checkout', '-b', $branchName, "origin/$branchName") | Out-Null
	} else {
		Write-Host (("`n[+] Creating new work branch '{0}' from main..." -f $branchName)) -ForegroundColor Cyan
		Invoke-GitCommand -Args @('checkout', '-b', $branchName) | Out-Null
	}

	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] You are now on work branch '{0}'." -f $branchName)) -ForegroundColor Green
	} else {
		Write-Warning 'Could not open the requested work branch. Review the git output above.'
	}

	Wait-ForUser
}

function Invoke-SaveShareWorkflow {
	Write-Host "`n[+] Preparing to save and share changes..." -ForegroundColor Cyan
	$currentBranch = Get-CurrentBranch

	if ($currentBranch -eq 'main') {
		Write-Warning "Do not save work directly on 'main'. Use option 1 to move to a work branch first."
		Wait-ForUser
		return
	}

	$commitMsg = Read-Host "Enter a short description of your changes (or press Enter for 'Update SOC Platform files')"
	if ([string]::IsNullOrWhiteSpace($commitMsg)) {
		$commitMsg = 'Update SOC Platform files'
	}

	$confirmSave = Read-Host (("This will commit staged changes on '{0}' and push to origin/{0}. Continue? (y/N)" -f $currentBranch))
	if ($confirmSave -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Save/share cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	& powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch $currentBranch -All -Message $commitMsg
	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] Successfully saved and pushed '{0}'." -f $currentBranch)) -ForegroundColor Green
	} else {
		Write-Warning 'Save/share workflow failed. Review the git output above.'
	}

	Wait-ForUser
}

function Invoke-OpenDemoWorkflow {
	$currentBranch = Get-CurrentBranch

	if ($currentBranch -ne $DemoBranch -and (Test-WorkingTreeDirty)) {
		Write-Warning 'You have unsaved changes on your current branch. Save them with option 2, or stash them manually, before opening demo mode.'
		Wait-ForUser
		return
	}

	Invoke-SafeSyncWorkflow -BranchName $DemoBranch
}

function Invoke-AddWorkToDemoWorkflow {
	$currentBranch = Get-CurrentBranch
	if ($currentBranch -ne $DemoBranch -and (Test-WorkingTreeDirty)) {
		Write-Warning 'You have unsaved changes on your current work branch. Save them with option 2, or stash them manually, before switching to staging.'
		Wait-ForUser
		return
	}

	$recommendedBranch = if ((Get-BranchCategory -BranchName $currentBranch) -eq 'work') { $currentBranch } else { Get-RecommendedWorkBranch }

	Write-Host "`n--- Push Work To Staging ---" -ForegroundColor $MenuSubheaderColor
	if ($recommendedBranch) {
		Write-Host (("1. Use recommended branch: {0}" -f $recommendedBranch)) -ForegroundColor $MenuTextColor
		Write-Host '2. Pick a different existing work branch' -ForegroundColor $MenuTextColor
		Write-Host '3. Cancel' -ForegroundColor $MenuTextColor
	} else {
		Write-Host '1. Pick an existing work branch' -ForegroundColor $MenuTextColor
		Write-Host '2. Cancel' -ForegroundColor $MenuTextColor
	}

	$workflowChoice = Read-Host 'Select an option'
	$sourceBranch = $null

	if ($recommendedBranch) {
		switch ($workflowChoice) {
			'1' { $sourceBranch = $recommendedBranch }
			'2' { $sourceBranch = Select-ExistingWorkBranch -RecommendedBranch $recommendedBranch -Title 'Choose the work branch to push to staging' }
			default { }
		}
	} else {
		switch ($workflowChoice) {
			'1' { $sourceBranch = Select-ExistingWorkBranch -Title 'Choose the work branch to push to staging' }
			default { }
		}
	}

	if ([string]::IsNullOrWhiteSpace($sourceBranch)) {
		Write-Warning 'Push-to-staging workflow cancelled.'
		Wait-ForUser
		return
	}

	if ($sourceBranch -eq $DemoBranch -or $sourceBranch -eq 'main') {
		Write-Warning 'Choose a work branch as the merge source.'
		Wait-ForUser
		return
	}

	if (-not (Switch-ToBranch -BranchName $DemoBranch)) {
		Wait-ForUser
		return
	}

	Write-Host (("`n[+] Syncing staging branch '{0}' before merge..." -f $DemoBranch)) -ForegroundColor Cyan
	powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $DemoBranch -AutoCheckpoint
	if ($LASTEXITCODE -ne 0) {
		Write-Warning 'Could not sync staging. Merge stopped.'
		Wait-ForUser
		return
	}

	$mergeMessage = Read-Host "Merge commit message (press Enter for 'Merge $sourceBranch into $DemoBranch')"
	if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
		$mergeMessage = "Merge $sourceBranch into $DemoBranch"
	}

	$confirmDemoMerge = Read-Host (("This will merge '{0}' into '{1}' and push to origin/{1}. Continue? (y/N)" -f $sourceBranch, $DemoBranch))
	if ($confirmDemoMerge -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Bring-work-into-demo cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	& powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch $DemoBranch -MergeFrom $sourceBranch -Message $mergeMessage
	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] '{0}' was pushed into staging branch '{1}'." -f $sourceBranch, $DemoBranch)) -ForegroundColor Green
	} else {
		Write-Warning 'Could not push work into staging. Review the git output above.'
	}

	Wait-ForUser
}

function Invoke-FinalMergeWorkflow {
	$confirm = Read-Host "Merge demo branch '$DemoBranch' into main now? This should only happen after team sign-off. (y/N)"
	if ($confirm -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Final merge cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	if (-not (Switch-ToBranch -BranchName 'main')) {
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

	$mergeMessage = Read-Host "Merge commit message (press Enter for 'Merge $DemoBranch into main')"
	if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
		$mergeMessage = "Merge $DemoBranch into main"
	}

	$confirmMainMerge = Read-Host (("This will merge '{0}' into 'main' and push to origin/main. Continue? (y/N)" -f $DemoBranch))
	if ($confirmMainMerge -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Final merge cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	& powershell.exe -ExecutionPolicy Bypass -File $PushScript -Branch main -MergeFrom $DemoBranch -Message $mergeMessage
	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] Demo branch '{0}' was merged into main and pushed." -f $DemoBranch)) -ForegroundColor Green
	} else {
		Write-Warning 'Final merge into main failed. Review the git output above.'
	}

	Wait-ForUser
}

Initialize-MenuTheme

while ($true) {
	$currentBranch = Get-CurrentBranch
	$startCodingLabel = if ($currentBranch -eq $DemoBranch) { 'Leave demo / start coding' } else { 'Start or continue coding' }
	$openDemoLabel = if ($currentBranch -eq $DemoBranch) { 'Refresh staging branch' } else { 'Open staging branch' }

	Write-Host "`n=================================" -ForegroundColor $MenuHeaderColor
	Write-Host '    SOC GIT WORKFLOW MENU        ' -ForegroundColor $MenuHeaderColor
	Write-Host '=================================' -ForegroundColor $MenuHeaderColor
	Write-Host 'Flow: main -> work branches -> staging -> main' -ForegroundColor $MenuSubheaderColor
	Show-Status
	Write-Host (("1. {0}" -f $startCodingLabel)) -ForegroundColor $MenuTextColor
	Write-Host '2. Save and share this branch' -ForegroundColor $MenuTextColor
	Write-Host (("3. {0}" -f $openDemoLabel)) -ForegroundColor $MenuTextColor
	Write-Host '4. Push work to staging' -ForegroundColor $MenuTextColor
	Write-Host '5. Ship approved demo to main' -ForegroundColor $MenuTextColor
	Write-Host '6. Branch help / where should I work?' -ForegroundColor $MenuTextColor
	Write-Host '7. Exit' -ForegroundColor $MenuTextColor
	Write-Host '=================================' -ForegroundColor $MenuHeaderColor

	$choice = Read-Host 'Select an option (1-7)'

	switch ($choice) {
		'1' { Invoke-StartCodingWorkflow }
		'2' { Invoke-SaveShareWorkflow }
		'3' { Invoke-OpenDemoWorkflow }
		'4' { Invoke-AddWorkToDemoWorkflow }
		'5' { Invoke-FinalMergeWorkflow }
		'6' { Show-ToolsMenu }
		'7' { exit }
		default { Write-Warning 'Invalid selection. Please choose 1-7.' }
	}
}
