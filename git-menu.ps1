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
		return 'START MODE'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'DEMO COMBINE MODE'
	}

	return 'CODING MODE'
}

function Get-BranchRoleLabel {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'MAIN BASELINE'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'STAGING / DEMO BRANCH'
	}

	return 'FEATURE / WORK BRANCH'
}

function Get-BranchPurpose {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'Stable baseline only. Do not do normal coding here.'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'Combine work here and prep the meeting demo.'
	}

	return 'Normal coding branch. Make changes here, then save/share them.'
}

function Get-BranchAccentColor {
	param(
		[Parameter(Mandatory = $true)][string]$BranchName
	)

	if ($BranchName -eq 'main') {
		return 'Red'
	}

	if ($BranchName -eq $DemoBranch) {
		return 'Cyan'
	}

	return 'Green'
}

function Show-FlowGraphic {
	$currentBranch = Get-CurrentBranch
	$roleLabel = Get-BranchRoleLabel -BranchName $currentBranch
	$roleColor = Get-BranchAccentColor -BranchName $currentBranch

	Write-Host 'Flow:' -ForegroundColor DarkGray -NoNewline
	Write-Host ' [MAIN] ' -ForegroundColor Red -NoNewline
	Write-Host '->' -ForegroundColor DarkGray -NoNewline
	Write-Host ' [WORK] ' -ForegroundColor Green -NoNewline
	Write-Host '->' -ForegroundColor DarkGray -NoNewline
	Write-Host ' [STAGING] ' -ForegroundColor Cyan -NoNewline
	Write-Host '->' -ForegroundColor DarkGray -NoNewline
	Write-Host ' [MAIN]' -ForegroundColor Red

	Write-Host 'Now :' -ForegroundColor DarkGray -NoNewline
	Write-Host (" [{0}] " -f $currentBranch.ToUpper()) -ForegroundColor $roleColor -NoNewline
	Write-Host (" {0}" -f $roleLabel) -ForegroundColor DarkGray
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

function Get-RemoteCleanupCandidates {
	$currentBranch = Get-CurrentBranch
	$mergedLocalWorkBranches = @(Get-MergedLocalWorkBranches)
	$remoteWorkBranches = @(Get-RemoteWorkBranches)

	$options = @()
	foreach ($branch in $remoteWorkBranches) {
		if ($branch -eq $currentBranch) {
			continue
		}

		if ((Test-LocalBranchExists -BranchName $branch) -and ($mergedLocalWorkBranches -notcontains $branch)) {
			continue
		}

		$description = if (Test-LocalBranchExists -BranchName $branch) {
			'merged locally too'
		} else {
			'remote only'
		}

		$options += [pscustomobject]@{
			Name = $branch
			Description = $description
		}
	}

	return @($options | Sort-Object Name)
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

	Write-Host "`n--- $Title ---" -ForegroundColor Cyan

	if ($Options.Count -eq 0) {
		Write-Host 'No branch options are available.' -ForegroundColor DarkGray
		return $null
	}

	for ($index = 0; $index -lt $Options.Count; $index++) {
		$option = $Options[$index]
		$details = if ([string]::IsNullOrWhiteSpace($option.Description)) { '' } else { " ($($option.Description))" }
		Write-Host (("{0}. {1}{2}" -f ($index + 1), $option.Name, $details)) -ForegroundColor White
	}

	Write-Host (("{0}. {1}" -f ($Options.Count + 1), $CancelLabel)) -ForegroundColor DarkGray

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

function Get-UnmergedFiles {
	return @(
		@(git diff --name-only --diff-filter=U) |
		Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
	)
}

function Test-HasUnmergedFiles {
	return (@(Get-UnmergedFiles).Count -gt 0)
}

function Test-MergeInProgress {
	$mergeHeadPath = (git rev-parse --git-path MERGE_HEAD 2>$null).Trim()
	if ([string]::IsNullOrWhiteSpace($mergeHeadPath)) {
		return $false
	}

	return (Test-Path $mergeHeadPath)
}

function Invoke-AbortCurrentMerge {
	if (-not (Test-MergeInProgress)) {
		Write-Warning 'No merge is currently in progress, so there is nothing to abort.'
		Wait-ForUser
		return
	}

	$confirmAbort = Read-Host 'Abort the current merge now and return this branch to its pre-merge state? (y/N)'
	if ($confirmAbort -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Merge abort cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	Invoke-GitCommand -Args @('merge', '--abort') | Out-Null
	if ($LASTEXITCODE -eq 0) {
		Write-Host "`n[+] Merge aborted successfully." -ForegroundColor Green
	} else {
		Write-Warning 'Could not abort the current merge. Review the git output above.'
	}

	Wait-ForUser
}

function Show-ConflictHelper {
	$currentBranch = Get-CurrentBranch
	$unmergedFiles = @(Get-UnmergedFiles)
	$mergeInProgress = Test-MergeInProgress

	Write-Host "`n--- CONFLICT HELPER ---" -ForegroundColor Red
	Write-Host (("Current branch: {0}" -f $currentBranch)) -ForegroundColor White
	Write-Host (("Merge in progress: {0}" -f ($(if ($mergeInProgress) { 'YES' } else { 'NO' })))) -ForegroundColor White
	Write-Host ''
	Write-Host 'Conflicted files:' -ForegroundColor Yellow
	if ($unmergedFiles.Count -eq 0) {
		Write-Host '  (none currently listed)' -ForegroundColor DarkGray
	} else {
		foreach ($file in $unmergedFiles) {
			Write-Host (("  - {0}" -f $file)) -ForegroundColor DarkGray
		}
	}

	Write-Host ''
	Write-Host 'What to do next on this branch:' -ForegroundColor Yellow
	if ((Get-BranchCategory -BranchName $currentBranch) -eq 'work') {
		Write-Host '  Resolve conflicts here on the work branch, test here, then use save/share when ready.' -ForegroundColor DarkGray
	} elseif ($currentBranch -eq $DemoBranch) {
		Write-Host '  You are on staging. In most cases, abort this merge and merge staging into your work branch instead.' -ForegroundColor DarkGray
	} else {
		Write-Host '  You are not on a normal work branch. Be careful about resolving here unless this branch is intentionally shared.' -ForegroundColor DarkGray
	}

	Write-Host ''
	Write-Host '1. Show raw git status' -ForegroundColor White
	Write-Host '2. Explain what to do on this branch' -ForegroundColor White
	Write-Host '3. Abort current merge' -ForegroundColor White
	Write-Host '4. Back' -ForegroundColor DarkGray

	$helperChoice = Read-Host 'Select an option (1-4)'
	switch ($helperChoice) {
		'1' {
			Write-Host "`n--- Raw Git Status ---" -ForegroundColor Yellow
			git status -sb
			Write-Host ''
			Wait-ForUser
		}
		'2' {
			Write-Host "`n--- Conflict Guidance ---" -ForegroundColor Yellow
			if ((Get-BranchCategory -BranchName $currentBranch) -eq 'work') {
				Write-Host '1. Open each conflicted file and decide whether to keep the work-branch version, the staging version, or a combined result.' -ForegroundColor DarkGray
				Write-Host '2. After editing a conflicted file, stage it with git add <file>.' -ForegroundColor DarkGray
				Write-Host '3. When all conflicted files are staged, commit the merge on this work branch.' -ForegroundColor DarkGray
				Write-Host '4. Then test here, save/share here, and only later push back to staging.' -ForegroundColor DarkGray
			} elseif ($currentBranch -eq $DemoBranch) {
				Write-Host '1. If this merge was accidental or too risky on staging, use option 3 here to abort it.' -ForegroundColor DarkGray
				Write-Host '2. Switch back to your work branch and use the staging-to-work-branch merge path instead.' -ForegroundColor DarkGray
				Write-Host '3. Resolve conflicts on the work branch, not on shared staging, unless you intentionally want to do that.' -ForegroundColor DarkGray
			} else {
				Write-Host '1. Decide whether this branch is the correct place to resolve the merge.' -ForegroundColor DarkGray
				Write-Host '2. If not, abort here and re-run the merge on the intended work branch.' -ForegroundColor DarkGray
				Write-Host '3. If yes, resolve files, stage them, and commit the merge before continuing.' -ForegroundColor DarkGray
			}
			Write-Host ''
			Wait-ForUser
		}
		'3' { Invoke-AbortCurrentMerge }
		default { }
	}
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
	$roleColor = Get-BranchAccentColor -BranchName $currentBranch
	$workGuidance = Get-WorkGuidance -BranchName $currentBranch

	Show-FlowGraphic
	Write-Host 'Code:' -ForegroundColor DarkGray -NoNewline
	Write-Host (" {0}" -f $workGuidance) -ForegroundColor $roleColor
	Write-Host 'Next:' -ForegroundColor DarkGray -NoNewline
	Write-Host (" {0}" -f $nextStep) -ForegroundColor White
	Write-Host 'Help:' -ForegroundColor DarkGray -NoNewline
	Write-Host ' 4 -> 2' -ForegroundColor Cyan -NoNewline
	Write-Host ((" | Changes: {0}" -f $changeCount)) -ForegroundColor DarkGray
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
	Write-Host (("Current branch: {0}" -f $currentBranch)) -ForegroundColor Yellow
	Write-Host 'main                = stable baseline only' -ForegroundColor DarkGray
	Write-Host 'feature/work branch = where you normally code' -ForegroundColor DarkGray
	Write-Host (("staging/demo branch = the one combined meeting branch ({0})" -f $DemoBranch)) -ForegroundColor DarkGray
	Write-Host ""
	Write-Host 'Normal flow: main -> feature/work branch -> staging/demo branch -> main' -ForegroundColor Yellow
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
	$roleColor = Get-BranchAccentColor -BranchName $currentBranch

	Write-Host "`n--- BRANCH PICKER ---" -ForegroundColor Cyan
	Show-FlowGraphic
	Write-Host 'Code:' -ForegroundColor DarkGray -NoNewline
	Write-Host (" {0}" -f (Get-WorkGuidance -BranchName $currentBranch)) -ForegroundColor $roleColor
	Write-Host ""
	Write-Host 'BEST' -ForegroundColor Cyan
	if ($currentBranch -eq 'main') {
		Write-Host '  MAIN -> use 1 for a work branch' -ForegroundColor White
	} elseif ($currentBranch -eq $DemoBranch) {
		Write-Host '  DEMO -> use 1 for a work branch' -ForegroundColor White
	} else {
		Write-Host (("  STAY -> {0}" -f $currentBranch)) -ForegroundColor White
	}

	Write-Host ""
	Write-Host 'WORK BRANCHES' -ForegroundColor Green
	if ($activeLocalWorkBranches.Count -eq 0) {
		Write-Host '  (none yet - use 1)' -ForegroundColor DarkGray
	} else {
		if ($currentBranch -eq 'main' -or $currentBranch -eq $DemoBranch) {
			Write-Host (("  > {0}" -f $recommendedBranch)) -ForegroundColor Green
			Write-Host ""
		}
		foreach ($branch in $activeLocalWorkBranches) {
			$marker = if ($branch -eq $currentBranch) { '*' } else { '-' }
			$suffix = if ($branch -eq $currentBranch) { ' <- current' } elseif ($branch -eq $recommendedBranch) { ' <- rec' } else { '' }
			$branchColor = if ($branch -eq $recommendedBranch) { 'Green' } else { 'White' }
			Write-Host (("  {0} {1}{2}" -f $marker, $branch, $suffix)) -ForegroundColor $branchColor
		}
	}

	Write-Host ""
	Write-Host (("DEMO   {0}" -f $DemoBranch)) -ForegroundColor Cyan

	Write-Host ""
	Write-Host 'MERGED ALREADY' -ForegroundColor Cyan
	if ($mergedLocalWorkBranches.Count -eq 0) {
		Write-Host '  (none)' -ForegroundColor DarkGray
	} else {
		foreach ($branch in $mergedLocalWorkBranches) {
			Write-Host (("  - {0}" -f $branch)) -ForegroundColor DarkGray
		}
	}

	Write-Host ""
	Write-Host 'REMOTE-ONLY' -ForegroundColor Cyan
	if ($remoteOnlyWorkBranches.Count -eq 0) {
		Write-Host '  (none visible)' -ForegroundColor DarkGray
	} else {
		foreach ($branch in $remoteOnlyWorkBranches) {
			Write-Host (("  - {0}" -f $branch)) -ForegroundColor DarkGray
		}
	}

	Write-Host ""
	Write-Host 'NAV' -ForegroundColor Cyan
	Write-Host '  1 = coding branch | 3 = demo actions' -ForegroundColor DarkGray
	Write-Host ""
	Wait-ForUser
}

function Show-CleanupCandidates {
	$localCleanupCandidates = @(Get-MergedLocalWorkBranches)
	$remoteCleanupCandidates = @(Get-RemoteCleanupCandidates)

	Write-Host "`n--- CLEANUP IDEAS ---" -ForegroundColor Cyan
	Write-Host 'LOCAL MERGED' -ForegroundColor Green
	if ($localCleanupCandidates.Count -eq 0) {
		Write-Host '  No local cleanup candidates found.' -ForegroundColor DarkGray
	} else {
		foreach ($branch in $localCleanupCandidates) {
			Write-Host (("  - {0}" -f $branch)) -ForegroundColor White
		}
	}

	Write-Host ""
	Write-Host 'REMOTE CANDIDATES' -ForegroundColor Cyan
	if ($remoteCleanupCandidates.Count -eq 0) {
		Write-Host '  No remote cleanup candidates found.' -ForegroundColor DarkGray
	} else {
		foreach ($branch in $remoteCleanupCandidates) {
			Write-Host (("  - {0} ({1})" -f $branch.Name, $branch.Description)) -ForegroundColor White
		}
	}

	Write-Host ""
	Write-Host 'Use this menu to delete one after review.' -ForegroundColor DarkGray
	Write-Host ""
	Wait-ForUser
}

function Invoke-DeleteLocalBranchWorkflow {
	$localCleanupCandidates = @(Get-MergedLocalWorkBranches)
	$options = @(
		$localCleanupCandidates |
		ForEach-Object {
			[pscustomobject]@{
				Name = $_
				Description = 'merged into main'
			}
		}
	)

	$branchToDelete = Select-BranchFromList -Title 'Delete a local merged branch' -Options $options
	if ([string]::IsNullOrWhiteSpace($branchToDelete)) {
		Write-Host 'Local branch deletion cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	$confirmDelete = Read-Host (("Delete local branch '{0}' now? This does not delete origin/{0}. (y/N)" -f $branchToDelete))
	if ($confirmDelete -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Local branch deletion cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	Invoke-GitCommand -Args @('branch', '-D', $branchToDelete) | Out-Null
	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] Deleted local branch '{0}'." -f $branchToDelete)) -ForegroundColor Green
	} else {
		Write-Warning 'Local branch deletion failed. Review the git output above.'
	}

	Wait-ForUser
}

function Invoke-DeleteRemoteBranchWorkflow {
	$remoteCleanupCandidates = @(Get-RemoteCleanupCandidates)
	$branchToDelete = Select-BranchFromList -Title 'Delete a remote branch' -Options $remoteCleanupCandidates
	if ([string]::IsNullOrWhiteSpace($branchToDelete)) {
		Write-Host 'Remote branch deletion cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	$confirmDelete = Read-Host (("Delete remote branch 'origin/{0}' now? This affects the shared repo. (y/N)" -f $branchToDelete))
	if ($confirmDelete -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Remote branch deletion cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	Invoke-GitCommand -Args @('push', 'origin', '--delete', $branchToDelete) | Out-Null
	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] Deleted remote branch 'origin/{0}'." -f $branchToDelete)) -ForegroundColor Green
	} else {
		Write-Warning 'Remote branch deletion failed. Review the git output above.'
	}

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

function Show-DemoMenu {
	while ($true) {
		$currentBranch = Get-CurrentBranch
		$openDemoLabel = if ($currentBranch -eq $DemoBranch) { 'Refresh demo branch' } else { 'Open demo branch' }

		Write-Host "`n--- DEMO ACTIONS ---" -ForegroundColor Cyan
		Write-Host (("1. {0}" -f $openDemoLabel)) -ForegroundColor White
		Write-Host '2. Push saved work to staging (direct merge)' -ForegroundColor White
		Write-Host '3. Merge staging into current work branch (review conflicts here)' -ForegroundColor White
		Write-Host '4. Merge staging into a selected work branch (review conflicts here)' -ForegroundColor White
		Write-Host '5. Ship approved demo to main' -ForegroundColor Red
		Write-Host '6. Back' -ForegroundColor DarkGray

		$demoChoice = Read-Host 'Select an option (1-6)'

		switch ($demoChoice) {
			'1' { Invoke-OpenDemoWorkflow }
			'2' { Invoke-AddWorkToDemoWorkflow }
			'3' { Invoke-MergeStagingIntoWorkBranch }
			'4' { Invoke-MergeStagingIntoSelectedWorkBranch }
			'5' { Invoke-FinalMergeWorkflow }
			'6' { return }
			default { Write-Warning 'Invalid selection. Please choose 1-6.' }
		}
	}
}

function Show-ToolsMenu {
	while ($true) {
		Write-Host "`n--- BRANCH HELP / CLEANUP ---" -ForegroundColor Cyan
		Write-Host '1. What do these branches mean?' -ForegroundColor White
		Write-Host '2. Which branch should I use right now?' -ForegroundColor White
		Write-Host '3. Which branches can I clean up?' -ForegroundColor White
		Write-Host '4. Delete a local merged branch' -ForegroundColor White
		Write-Host '5. Delete a remote branch' -ForegroundColor Red
		Write-Host '6. Remove stale remote branch refs' -ForegroundColor White
		Write-Host '7. Show raw git status' -ForegroundColor White
		Write-Host '8. Back' -ForegroundColor DarkGray

		$toolChoice = Read-Host 'Select an option (1-8)'

		switch ($toolChoice) {
			'1' { Show-BranchGuide }
			'2' { Show-BranchChooserHelp }
			'3' { Show-CleanupCandidates }
			'4' { Invoke-DeleteLocalBranchWorkflow }
			'5' { Invoke-DeleteRemoteBranchWorkflow }
			'6' { Invoke-PruneRemoteRefs }
			'7' { Show-DetailedStatus }
			'8' { return }
			default { Write-Warning 'Invalid selection. Please choose 1-8.' }
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
		Write-Host "`n--- Main Has Unsaved Changes ---" -ForegroundColor Yellow
		Write-Host 'You already have uncommitted changes on main.' -ForegroundColor Yellow
		Write-Host 'Those changes should go onto a work branch before you continue.' -ForegroundColor Yellow
		Write-Host '1. Create a new work branch from these current changes' -ForegroundColor Yellow
		Write-Host '2. Cancel' -ForegroundColor Yellow

		$dirtyMainChoice = Read-Host 'Select an option (1-2)'
		if ($dirtyMainChoice -ne '1') {
			Write-Warning 'Start-coding workflow cancelled.'
			Wait-ForUser
			return
		}

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
	Write-Host "`n--- Start Or Continue Coding ---" -ForegroundColor Yellow
	if ($recommendedBranch) {
		Write-Host (("1. Use recommended branch: {0}" -f $recommendedBranch)) -ForegroundColor Yellow
		Write-Host '2. Pick a different existing branch' -ForegroundColor Yellow
		Write-Host '3. Create a new branch' -ForegroundColor Yellow
		Write-Host '4. Cancel' -ForegroundColor Yellow
	} else {
		Write-Host '1. Pick an existing branch' -ForegroundColor Yellow
		Write-Host '2. Create a new branch' -ForegroundColor Yellow
		Write-Host '3. Cancel' -ForegroundColor Yellow
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

function Invoke-MergeStagingIntoWorkBranch {
	$currentBranch = Get-CurrentBranch
	if ((Get-BranchCategory -BranchName $currentBranch) -ne 'work') {
		Write-Warning 'You are not currently on a normal work branch. Use the selected-branch option instead.'
		Wait-ForUser
		return
	}

	Invoke-MergeStagingIntoTargetWorkBranch -TargetBranch $currentBranch
}

function Invoke-MergeStagingIntoSelectedWorkBranch {
	$currentBranch = Get-CurrentBranch
	$recommendedBranch = if ((Get-BranchCategory -BranchName $currentBranch) -eq 'work') { $currentBranch } else { Get-RecommendedWorkBranch }
	$targetBranch = Select-ExistingWorkBranch -RecommendedBranch $recommendedBranch -Title 'Choose the work branch that should receive staging changes'
	if ([string]::IsNullOrWhiteSpace($targetBranch)) {
		Write-Warning 'No work branch selected. Merge-from-staging cancelled.'
		Wait-ForUser
		return
	}

	Invoke-MergeStagingIntoTargetWorkBranch -TargetBranch $targetBranch
}

function Invoke-MergeStagingIntoTargetWorkBranch {
	param(
		[Parameter(Mandatory = $true)][string]$TargetBranch
	)

	if ([string]::IsNullOrWhiteSpace($TargetBranch)) {
		Write-Warning 'No target work branch was provided.'
		Wait-ForUser
		return
	}

	if ($TargetBranch -eq 'main' -or $TargetBranch -eq $DemoBranch) {
		Write-Warning 'Choose a normal work branch as the merge target.'
		Wait-ForUser
		return
	}

	$currentBranch = Get-CurrentBranch
	if (Test-WorkingTreeDirty) {
		Write-Warning 'Your current branch has unsaved changes. Save them with option 2, or stash them manually, before merging staging into a work branch.'
		Wait-ForUser
		return
	}

	Write-Host ""
	Write-Host (("This will sync '{0}', switch to work branch '{1}', and merge staging into that work branch for conflict review." -f $DemoBranch, $TargetBranch)) -ForegroundColor Yellow
	Write-Host 'This does not push anything automatically. Resolve any conflicts on the work branch, test there, then save/share and push to staging later.' -ForegroundColor DarkGray
	$confirmWorkMerge = Read-Host ("Continue merging '{0}' into '{1}'? (y/N)" -f $DemoBranch, $TargetBranch)
	if ($confirmWorkMerge -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Merge-from-staging cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	if (-not (Switch-ToBranch -BranchName $DemoBranch)) {
		Wait-ForUser
		return
	}

	Write-Host (("`n[+] Syncing staging branch '{0}' before merging it into '{1}'..." -f $DemoBranch, $TargetBranch)) -ForegroundColor Cyan
	powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $DemoBranch -AutoCheckpoint
	if ($LASTEXITCODE -ne 0) {
		Write-Warning 'Could not sync staging. Merge stopped.'
		Wait-ForUser
		return
	}

	if (-not (Switch-ToBranch -BranchName $TargetBranch)) {
		Wait-ForUser
		return
	}

	$mergeMessage = Read-Host ("Merge commit message (press Enter for 'Merge {0} into {1}')" -f $DemoBranch, $TargetBranch)
	if ([string]::IsNullOrWhiteSpace($mergeMessage)) {
		$mergeMessage = "Merge $DemoBranch into $TargetBranch"
	}

	Write-Host (("`n[+] Merging staging branch '{0}' into work branch '{1}'..." -f $DemoBranch, $TargetBranch)) -ForegroundColor Cyan
	Invoke-GitCommand -Args @('merge', '--no-ff', $DemoBranch, '-m', $mergeMessage) | Out-Null
	if ($LASTEXITCODE -eq 0) {
		Write-Host (("`n[+] Staging branch '{0}' was merged into work branch '{1}'. Review/test here, then use option 2 to save/share when ready." -f $DemoBranch, $TargetBranch)) -ForegroundColor Green
	} else {
		Write-Warning 'Merge from staging did not complete cleanly. Resolve conflicts on the work branch, then save/share when ready.'
	}

	Wait-ForUser
}

function Invoke-AddWorkToDemoWorkflow {
	$currentBranch = Get-CurrentBranch
	if ($currentBranch -ne $DemoBranch -and (Test-WorkingTreeDirty)) {
		Write-Warning 'You have unsaved changes on your current work branch. Save them with option 2, or stash them manually, before switching to the demo branch.'
		Wait-ForUser
		return
	}

	$recommendedBranch = if ((Get-BranchCategory -BranchName $currentBranch) -eq 'work') { $currentBranch } else { Get-RecommendedWorkBranch }

	Write-Host "`n--- Push Saved Work To Staging ---" -ForegroundColor Yellow
	if ($recommendedBranch) {
		Write-Host (("1. Use recommended branch: {0}" -f $recommendedBranch)) -ForegroundColor Yellow
		Write-Host '2. Pick a different existing work branch' -ForegroundColor Yellow
		Write-Host '3. Cancel' -ForegroundColor Yellow
	} else {
		Write-Host '1. Pick an existing work branch' -ForegroundColor Yellow
		Write-Host '2. Cancel' -ForegroundColor Yellow
	}

	$workflowChoice = Read-Host 'Select an option'
	$sourceBranch = $null

	if ($recommendedBranch) {
		switch ($workflowChoice) {
			'1' { $sourceBranch = $recommendedBranch }
			'2' { $sourceBranch = Select-ExistingWorkBranch -RecommendedBranch $recommendedBranch -Title 'Choose the work branch to bring into demo' }
			default { }
		}
	} else {
		switch ($workflowChoice) {
			'1' { $sourceBranch = Select-ExistingWorkBranch -Title 'Choose the work branch to bring into demo' }
			default { }
		}
	}

	if ([string]::IsNullOrWhiteSpace($sourceBranch)) {
		Write-Warning 'Bring-work-into-demo workflow cancelled.'
		Wait-ForUser
		return
	}

	if ($sourceBranch -eq $DemoBranch -or $sourceBranch -eq 'main') {
		Write-Warning 'Choose a work branch as the merge source.'
		Wait-ForUser
		return
	}

	Write-Host ""
	Write-Host 'This path directly merges a saved work branch into staging.' -ForegroundColor Yellow
	Write-Host 'If you expect overlap with recent staging changes, cancel here and merge staging into your work branch first.' -ForegroundColor DarkGray
	$confirmDirectMerge = Read-Host 'Continue with direct merge into staging? (y/N)'
	if ($confirmDirectMerge -notin @('y', 'Y', 'yes', 'YES')) {
		Write-Host 'Push-to-staging cancelled.' -ForegroundColor Yellow
		Wait-ForUser
		return
	}

	if (-not (Switch-ToBranch -BranchName $DemoBranch)) {
		Wait-ForUser
		return
	}

	Write-Host (("`n[+] Syncing demo branch '{0}' before merge..." -f $DemoBranch)) -ForegroundColor Cyan
	powershell.exe -ExecutionPolicy Bypass -File $SyncScript -UpstreamBranch $DemoBranch -AutoCheckpoint
	if ($LASTEXITCODE -ne 0) {
		Write-Warning 'Could not sync the demo branch. Merge stopped.'
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
		Write-Host (("`n[+] '{0}' was merged into demo branch '{1}'." -f $sourceBranch, $DemoBranch)) -ForegroundColor Green
	} else {
		Write-Warning 'Could not add work to the demo branch. Review the git output above.'
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

while ($true) {
	$currentBranch = Get-CurrentBranch
	$startCodingLabel = if ($currentBranch -eq $DemoBranch) { 'Leave demo / start coding' } else { 'Start or continue coding' }
	$hasUnmergedFiles = Test-HasUnmergedFiles

	Write-Host "`n+---------------------------------------+" -ForegroundColor Cyan
	Write-Host '  SOC GIT WORKFLOW MENU' -ForegroundColor Cyan
	Write-Host '+---------------------------------------+' -ForegroundColor Cyan
	Show-Status
	if ($hasUnmergedFiles) {
		Write-Host ''
		Write-Host 'Conflict detected: unresolved merge files are present.' -ForegroundColor Red
		Write-Host 'Use Conflict helper before continuing with more branch actions.' -ForegroundColor DarkGray
	}
	Write-Host ''
	Write-Host (("1. {0}" -f $startCodingLabel)) -ForegroundColor White
	Write-Host '2. Save and share this work branch' -ForegroundColor White
	Write-Host '3. Staging branch actions' -ForegroundColor White
	Write-Host '4. Branch help / cleanup' -ForegroundColor White
	if ($hasUnmergedFiles) {
		Write-Host '5. Conflict helper' -ForegroundColor White
		Write-Host '6. Exit' -ForegroundColor DarkGray
	} else {
		Write-Host '5. Exit' -ForegroundColor DarkGray
	}
	Write-Host '+---------------------------------------+' -ForegroundColor Cyan

	$choice = if ($hasUnmergedFiles) { Read-Host 'Select an option (1-6)' } else { Read-Host 'Select an option (1-5)' }

	switch ($choice) {
		'1' { Invoke-StartCodingWorkflow }
		'2' { Invoke-SaveShareWorkflow }
		'3' { Show-DemoMenu }
		'4' { Show-ToolsMenu }
		'5' {
			if ($hasUnmergedFiles) {
				Show-ConflictHelper
			} else {
				exit
			}
		}
		'6' {
			if ($hasUnmergedFiles) {
				exit
			} else {
				Write-Warning 'Invalid selection. Please choose 1-5.'
			}
		}
		default {
			if ($hasUnmergedFiles) {
				Write-Warning 'Invalid selection. Please choose 1-6.'
			} else {
				Write-Warning 'Invalid selection. Please choose 1-5.'
			}
		}
	}
}
