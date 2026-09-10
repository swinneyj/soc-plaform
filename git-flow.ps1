<#
.SYNOPSIS
  Multi-user safe git flow: personal staging -> origin/staging -> main

.DESCRIPTION
  Branch model
    main              shared production trunk (protected)
    staging           shared integration (origin/staging)
    staging-<you>     YOUR only write target for unfinished work

  Daily flow
    1. Work on staging-<you>
    2. Push staging-<you> often
    3. End of day: merge staging-<you> -> staging
    4. When staging is green: promote staging -> main (gated)

.EXAMPLE
  .\git-flow.ps1
  .\git-flow.ps1 -UserName dalton
#>

[CmdletBinding()]
param(
    [string] ${MainBranch} = "main",
    [string] ${SharedStaging} = "staging",
    [string] ${UserName} = "",
    [string] ${GitSyncPath} = ".\git-sync.ps1"
)

$ErrorActionPreference = "Stop"

function Get-DefaultUserName {
    $u = ${UserName}
    if (-not $u) { $u = $env:GIT_FLOW_USER }
    if (-not $u) { $u = $env:USERNAME }
    if (-not $u) { $u = $env:USER }
    if (-not $u) { $u = "dev" }
    $u = $u.ToLower() -replace '[^a-z0-9_-]', '-'
    return $u
}

$Script:Me = Get-DefaultUserName
$Script:MyStaging = "staging-$($Script:Me)"

function Write-Header {
    param([string] $Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Get-CurrentBranch {
    return (git rev-parse --abbrev-ref HEAD).Trim()
}

function Assert-Repo {
    $inside = git rev-parse --is-inside-work-tree 2>$null
    if (-not $inside) {
        throw "Not inside a git repo. cd to the project root first."
    }
}

function Test-WorkingTreeDirty {
    $status = git status --porcelain
    return [bool]$status
}

function Ensure-CleanForBranchSwitch {
    <#
      Returns:
        $true  - safe to switch branches (clean, or user chose continue/stash handled by caller)
        $false - abort
      Sets $Script:DidStash = $true if we stashed for the caller to pop later.
    #>
    $Script:DidStash = $false
    if (-not (Test-WorkingTreeDirty)) {
        return $true
    }

    Write-Host "Working tree has uncommitted changes:" -ForegroundColor Yellow
    git status -sb
    Write-Host ""
    Write-Host "  [S] Stash, continue, then stash pop when done (recommended for tools like git-flow.ps1)"
    Write-Host "  [C] Commit on current branch first (abort this action so you can menu 1 / commit)"
    Write-Host "  [Y] Continue anyway (may fail if checkout would overwrite files)"
    Write-Host "  [N] Abort"
    $r = Read-Host "Choose"
    switch -Regex ($r) {
        '^[Ss]$' {
            $stashMsg = "git-flow auto-stash $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
            git stash push -u -m $stashMsg
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Stash failed - aborting." -ForegroundColor Red
                return $false
            }
            $Script:DidStash = $true
            Write-Host "Stashed. Will try to pop when this action finishes." -ForegroundColor Green
            return $true
        }
        '^[Cc]$' {
            Write-Host "Aborted. Commit your changes (menu 1 or git commit), then retry." -ForegroundColor Yellow
            return $false
        }
        '^[Yy]$' {
            return $true
        }
        default {
            Write-Host "Aborted." -ForegroundColor Yellow
            return $false
        }
    }
}

function Restore-StashIfNeeded {
    if (-not $Script:DidStash) {
        return
    }
    Write-Host "Restoring stashed changes (git stash pop)..." -ForegroundColor Cyan
    git stash pop
    if ($LASTEXITCODE -ne 0) {
        Write-Host "stash pop had conflicts or failed. Resolve with: git status / git stash list" -ForegroundColor Yellow
    }
    else {
        Write-Host "Stash restored." -ForegroundColor Green
    }
    $Script:DidStash = $false
}

# Back-compat name used by older call sites
function Ensure-CleanOrConfirm {
    return (Ensure-CleanForBranchSwitch)
}

function Get-CommitMessage {
    param([string] $Default)
    $msg = Read-Host "Message (Enter = '$Default')"
    if ([string]::IsNullOrWhiteSpace($msg)) {
        return $Default
    }
    return $msg.Trim()
}

function Invoke-GitSync {
    param(
        [Parameter(Mandatory = $true)][string] $Branch,
        [string] $Message = "",
        [string] $MergeFrom = "",
        [switch] $PreferRemote
    )
    if (-not (Test-Path ${GitSyncPath})) {
        return $false
    }
    $argList = @("-Branch", $Branch)
    if ($Message) {
        $argList += @("-Message", $Message)
    }
    if ($MergeFrom) {
        $argList += @("-MergeFrom", $MergeFrom)
    }
    if ($PreferRemote) {
        $argList += "-PreferRemote"
    }
    & ${GitSyncPath} @argList
    return $true
}

function Ensure-Branch-FromMain {
    param([Parameter(Mandatory = $true)][string] $Name)
    git fetch origin
    $local = git branch --list $Name
    $remote = git branch -r --list "origin/$Name"
    if ($local) {
        git checkout $Name
        return
    }
    if ($remote) {
        git checkout -b $Name "origin/$Name"
        return
    }
    git checkout ${MainBranch}
    git pull origin ${MainBranch}
    git checkout -b $Name ${MainBranch}
    Write-Host "Created $Name from ${MainBranch}" -ForegroundColor Green
}

function Save-MyStaging {
    Write-Header "Save and push $($Script:MyStaging)"
    Ensure-Branch-FromMain -Name $Script:MyStaging
    git status -sb

    $msg = Get-CommitMessage "wip: $($Script:MyStaging)"
    git add -A
    $staged = git diff --cached --name-only
    if ($staged) {
        git commit -m $msg
        Write-Host "Committed." -ForegroundColor Green
    }
    else {
        Write-Host "Nothing new to commit." -ForegroundColor DarkGray
    }

    $tagAns = Read-Host "Create safety tag? [y/N]"
    if ($tagAns -match '^[Yy]') {
        $tag = "backup/$($Script:MyStaging)-$(Get-Date -Format yyyyMMdd-HHmm)"
        git tag $tag
        Write-Host "Tagged $tag"
        $pushTag = Read-Host "Push tag? [y/N]"
        if ($pushTag -match '^[Yy]') {
            git push origin $tag
        }
    }

    Write-Host "Pushing $($Script:MyStaging)..."
    # Plain git only. git-sync.ps1 has been observed passing -Message into git fetch.
    git push -u origin $Script:MyStaging
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Push failed. Check remote permissions / network." -ForegroundColor Red
        return
    }
    Write-Host "Done. Your work is on origin/$($Script:MyStaging)" -ForegroundColor Green
}

function Show-Compare {
    Write-Header "Compare branches"
    git fetch origin

    Write-Host ""
    Write-Host "--- $($Script:MyStaging) vs origin/${SharedStaging} ---" -ForegroundColor Cyan
    Write-Host "Only on YOUR staging (not in shared staging yet):"
    git log --oneline "origin/${SharedStaging}..$($Script:MyStaging)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        git log --oneline "origin/${SharedStaging}..origin/$($Script:MyStaging)" 2>$null
    }
    Write-Host "Only on shared staging (you may need to merge these in):"
    git log --oneline "$($Script:MyStaging)..origin/${SharedStaging}" 2>$null

    Write-Host ""
    Write-Host "--- origin/${SharedStaging} vs origin/${MainBranch} ---" -ForegroundColor Cyan
    Write-Host "On staging, not in main (ready to promote when green):"
    git log --oneline "origin/${MainBranch}..origin/${SharedStaging}" 2>$null
    Write-Host "On main, not in staging (staging is behind main):"
    git log --oneline "origin/${SharedStaging}..origin/${MainBranch}" 2>$null

    Write-Host ""
    Write-Host "--- Other personal staging branches on origin ---" -ForegroundColor Cyan
    git branch -r | Select-String "origin/staging-"
}

function Invoke-GitQuiet {
    # git writes progress to stderr; with $ErrorActionPreference=Stop that becomes a terminating error in PS.
    param([Parameter(ValueFromRemainingArguments = $true)]$GitArgs)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & git @GitArgs 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            Write-Host $_.Exception.Message
        }
        else {
            Write-Host $_
        }
    }
    $script:LastGitExit = $LASTEXITCODE
    $ErrorActionPreference = $old
    return $script:LastGitExit
}

function Update-SharedStaging-FromMain {
    Write-Header "Refresh origin/${SharedStaging} from ${MainBranch}"
    if (-not (Ensure-CleanForBranchSwitch)) {
        return
    }
    $startBranch = Get-CurrentBranch
    try {
        Invoke-GitQuiet fetch origin

        # 1) Update local main from origin
        $rc = Invoke-GitQuiet checkout ${MainBranch}
        if ($rc -ne 0) {
            Write-Host "Could not checkout ${MainBranch}." -ForegroundColor Red
            return
        }
        Invoke-GitQuiet pull origin ${MainBranch}

        # 2) Catch up local staging to origin/staging FIRST
        $rc = Invoke-GitQuiet checkout ${SharedStaging}
        if ($rc -ne 0) {
            $remoteStaging = git branch -r --list "origin/${SharedStaging}"
            if ($remoteStaging) {
                Invoke-GitQuiet checkout -b ${SharedStaging} "origin/${SharedStaging}"
            }
            else {
                Invoke-GitQuiet checkout -b ${SharedStaging} ${MainBranch}
            }
        }
        Invoke-GitQuiet pull origin ${SharedStaging}

        # 3) Merge main into staging
        $rc = Invoke-GitQuiet merge ${MainBranch} -m "Sync ${SharedStaging} with ${MainBranch}"
        if ($rc -ne 0) {
            Write-Host "Conflicts - resolve on ${SharedStaging}, commit, then: git push origin ${SharedStaging}" -ForegroundColor Red
            return
        }

        $rc = Invoke-GitQuiet push origin ${SharedStaging}
        if ($rc -ne 0) {
            Write-Host "Push rejected. Try: git pull origin ${SharedStaging} then push again." -ForegroundColor Red
            return
        }
        Write-Host "origin/${SharedStaging} is up to date with main." -ForegroundColor Green
    }
    finally {
        if ($startBranch -and ((Get-CurrentBranch) -ne $startBranch)) {
            Invoke-GitQuiet checkout $startBranch | Out-Null
        }
        Restore-StashIfNeeded
    }
}

function Merge-MyStaging-Into-Shared {
    Write-Header "Merge $($Script:MyStaging) into ${SharedStaging}"
    if (-not (Ensure-CleanForBranchSwitch)) {
        return
    }
    $startBranch = Get-CurrentBranch
    try {
        git fetch origin

        Ensure-Branch-FromMain -Name $Script:MyStaging
        git push -u origin $Script:MyStaging 2>$null

        Ensure-Branch-FromMain -Name ${SharedStaging}
        git pull origin ${SharedStaging}

        Write-Host "Commits that will merge into ${SharedStaging}:"
        git log --oneline "${SharedStaging}..$($Script:MyStaging)"
        $go = Read-Host "Merge $($Script:MyStaging) into ${SharedStaging}? [Y/n]"
        if ($go -match '^[Nn]') {
            return
        }

        $msg = Get-CommitMessage "Merge $($Script:MyStaging) into ${SharedStaging}"
        git merge $Script:MyStaging -m $msg
        if ($LASTEXITCODE -ne 0) {
            Write-Host "CONFLICT - fix files, git add -A, git commit, then push ${SharedStaging}" -ForegroundColor Red
            Write-Host "You are NOT on main; conflicts are safe to resolve here." -ForegroundColor Yellow
            return
        }

        git push origin ${SharedStaging}
        Write-Host "Pushed origin/${SharedStaging}" -ForegroundColor Green
    }
    finally {
        if ($startBranch -and ((Get-CurrentBranch) -ne $startBranch)) {
            git checkout $startBranch 2>$null
        }
        Restore-StashIfNeeded
    }
}

function Test-MainPromoteChecklist {
    Write-Host ""
    Write-Host "MAIN PROMOTE CHECKLIST - answer y/N for each. Any N aborts." -ForegroundColor Yellow
    Write-Host "origin/${MainBranch} should almost never be pushed without these." -ForegroundColor DarkYellow
    Write-Host ""

    $checks = @(
        "Shared staging (${SharedStaging}) was exercised on a running instance (Analysis, evidence timeline, no obvious break)",
        "Compared staging vs main (menu 2) and the commit list looks intentional",
        "No known WIP left only on a personal staging-* branch that still needs to land",
        "Teammates who share this repo were notified / not mid-push to staging",
        "Safety tag or known-good SHA exists on staging (or you accept risk)"
    )

    foreach ($prompt in $checks) {
        $a = Read-Host "  [ ] $prompt  [y/N]"
        if ($a -notmatch '^[Yy]') {
            Write-Host "Aborted: checklist item failed." -ForegroundColor Red
            return $false
        }
    }

    Write-Host ""
    Write-Host "Final gate: type  PROMOTE MAIN  exactly to allow merge+push to origin/${MainBranch}" -ForegroundColor Red
    $final = Read-Host "Confirm phrase"
    if ($final -cne "PROMOTE MAIN") {
        Write-Host "Aborted: confirmation phrase mismatch." -ForegroundColor Red
        return $false
    }
    return $true
}

function Merge-SharedStaging-Into-Main {
    Write-Header "Promote ${SharedStaging} to ${MainBranch} (GATED)"
    Write-Host "Direct day-to-day pushes to origin/${MainBranch} are discouraged." -ForegroundColor Yellow
    Write-Host "This path is the only menu option that updates main - and only after checklist + phrase." -ForegroundColor Yellow

    if (-not (Ensure-CleanOrConfirm)) {
        return
    }

    git fetch origin

    $ahead = 0
    try {
        $ahead = [int]((git rev-list --count "${MainBranch}..origin/${SharedStaging}").Trim())
    }
    catch {
        $ahead = -1
    }
    if ($ahead -eq 0) {
        Write-Host "Nothing to promote: origin/${SharedStaging} is not ahead of ${MainBranch}." -ForegroundColor DarkGray
        return
    }

    Write-Host ""
    Write-Host "Commits that would land on ${MainBranch} ($ahead):" -ForegroundColor Cyan
    git log --oneline "${MainBranch}..origin/${SharedStaging}"

    if (-not (Test-MainPromoteChecklist)) {
        return
    }

    git checkout ${MainBranch}
    git pull origin ${MainBranch}

    $msg = Get-CommitMessage "Merge ${SharedStaging} into ${MainBranch}"
    git merge "origin/${SharedStaging}" -m $msg
    if ($LASTEXITCODE -ne 0) {
        Write-Host "CONFLICT on main - resolve carefully, then: git push origin ${MainBranch}" -ForegroundColor Red
        Write-Host "Do not reset main to discard teammate work." -ForegroundColor Yellow
        return
    }

    Write-Host "Pushing origin/${MainBranch} ..." -ForegroundColor Yellow
    git push origin ${MainBranch}
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Push rejected. If the remote protects main, open a PR: ${SharedStaging} -> ${MainBranch} instead of pushing." -ForegroundColor Red
        Write-Host "  gh pr create --base ${MainBranch} --head ${SharedStaging}" -ForegroundColor DarkGray
        return
    }
    Write-Host "origin/${MainBranch} updated from ${SharedStaging}." -ForegroundColor Green
}

function Sync-MyStaging-With-Shared {
    Write-Header "Update $($Script:MyStaging) from origin/${SharedStaging} (get teammates work)"
    if (-not (Ensure-CleanOrConfirm)) {
        return
    }
    git fetch origin
    Ensure-Branch-FromMain -Name $Script:MyStaging
    $mergeMsg = "Merge origin/${SharedStaging} into $($Script:MyStaging)"
    git merge "origin/${SharedStaging}" -m $mergeMsg
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Conflicts - resolve on YOUR staging branch, then push." -ForegroundColor Red
        return
    }
    git push origin $Script:MyStaging
    Write-Host "$($Script:MyStaging) now includes shared staging." -ForegroundColor Green
}

function Prefer-RemoteDangerous {
    Write-Header "DANGER: reset local branch to origin (discards local commits)"
    $b = Read-Host "Branch name to reset (or empty to cancel)"
    if ([string]::IsNullOrWhiteSpace($b)) {
        return
    }
    if ($b -eq ${MainBranch} -or $b -eq ${SharedStaging}) {
        Write-Host "Refusing silent reset of $b. Type YES in caps to force." -ForegroundColor Red
        if ((Read-Host "Confirm") -cne "YES") {
            return
        }
    }
    else {
        $c = Read-Host "Type the branch name again to confirm ($b)"
        if ($c -ne $b) {
            Write-Host "Cancelled."
            return
        }
    }
    git fetch origin
    git checkout $b
    git reset --hard "origin/$b"
    Write-Host "Local $b == origin/$b" -ForegroundColor Yellow
}

function Show-Menu {
    Write-Host ""
    Write-Host "SOC git-flow  (multi-user staging)" -ForegroundColor Cyan
    Write-Host "  You              : $($Script:Me)"
    Write-Host "  Your branch      : $($Script:MyStaging)   <- write here daily"
    Write-Host "  Shared staging   : ${SharedStaging}"
    Write-Host "  Main             : ${MainBranch}"
    Write-Host "  Current HEAD     : $(Get-CurrentBranch)"
    Write-Host ""
    Write-Host "  1) Save and push MY staging ($($Script:MyStaging))"
    Write-Host "  2) Compare (mine vs staging vs main)"
    Write-Host "  3) Pull teammates work into MY staging (merge origin/staging)"
    Write-Host "  4) Publish MY work to shared staging (merge mine -> staging + push)"
    Write-Host "  5) Refresh shared staging from main"
    Write-Host "  6) Promote staging -> main (GATED checklist - rare)"
    Write-Host "  7) DANGER: hard-reset local branch to origin"
    Write-Host "  Q) Quit"
    Write-Host ""
    Write-Host "  Policy: do not push origin/${MainBranch} from ad-hoc commands; use 6 only when staging is verified." -ForegroundColor DarkGray
}

Assert-Repo
Write-Host "Using personal staging branch: $($Script:MyStaging)" -ForegroundColor DarkGray
Write-Host "Override with -UserName dalton  or  `$env:GIT_FLOW_USER='dalton'" -ForegroundColor DarkGray

$Script:KeepRunning = $true
while ($Script:KeepRunning) {
    Show-Menu
    $choice = Read-Host "Choose"
    switch -Regex ($choice) {
        '^1$' { Save-MyStaging }
        '^2$' { Show-Compare }
        '^3$' { Sync-MyStaging-With-Shared }
        '^4$' { Merge-MyStaging-Into-Shared }
        '^5$' { Update-SharedStaging-FromMain }
        '^6$' { Merge-SharedStaging-Into-Main }
        '^7$' { Prefer-RemoteDangerous }
        '^[Qq]$' { $Script:KeepRunning = $false }
        default { Write-Host "Unknown option" -ForegroundColor Yellow }
    }
}

Write-Host "Done."
