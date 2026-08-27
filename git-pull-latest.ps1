param (
    [string]$Branch = "main",
    [switch]$StashLocal = $true
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

    # Check current branch
    $currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($currentBranch -ne $Branch) {
        throw "Refusing to pull into branch '$currentBranch'. Expected '$Branch'. Use -Branch to override if intentional."
    }

    # Optionally stash local changes to avoid conflicts
    $hadStash = $false
    if ($StashLocal) {
        Write-Host "[*] Stashing local changes (if any) to prevent conflicts..." -ForegroundColor Cyan
        # Only stash if there are local modifications
        git diff --quiet --ignore-submodules HEAD
        if ($LASTEXITCODE -ne 0) {
            Invoke-GitSafe -Command "git stash push -u -m `"pre-pull-auto-stash`"" -ErrorMessage "Failed to stash local changes."
            $hadStash = $true
        } else {
            Write-Host "[+] No local changes detected; skipping stash." -ForegroundColor Green
        }
    }

    # Fetch + pull with rebase to keep history clean
    Write-Host "[*] Fetching latest from origin..." -ForegroundColor Cyan
    Invoke-GitSafe -Command "git fetch origin" -ErrorMessage "git fetch failed. Check network/remote."

    Write-Host "[*] Pulling latest changes (rebase) from origin/$Branch..." -ForegroundColor Cyan
    Invoke-GitSafe -Command "git pull --rebase origin $Branch" -ErrorMessage "git pull --rebase failed. Resolve conflicts, then re-run git-pull-latest.ps1."

    # Restore stashed work on top
    if ($hadStash) {
        Write-Host "[*] Restoring your local changes on top of updated code..." -ForegroundColor Cyan
        git stash pop
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Stash pop encountered conflicts. Resolve them (git status, fix files, git add, git rebase --continue), then continue working."
        } else {
            Write-Host "[+] Local changes restored cleanly." -ForegroundColor Green
        }
    } else {
        Write-Host "[+] Successfully updated from origin/$Branch. No local changes to restore." -ForegroundColor Green
    }
}
catch {
    Write-Warning $_.Exception.Message
    exit 1
}
