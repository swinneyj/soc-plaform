function Do-Pull {
    Write-Host "
[+] Stashing local changes..." -ForegroundColor Cyan
    git stash
    Write-Host "[+] Pulling latest updates from origin main..." -ForegroundColor Cyan
    git pull origin main

    if ($LASTEXITCODE -eq 0) {
        $hasStash = git stash list
        if ($hasStash) {
            Write-Host "[+] Restoring your local changes..." -ForegroundColor Green
            git stash pop
        } else {
            Write-Host "✓ Successfully updated! No local changes to restore." -ForegroundColor Green
        }
    } else {
        Write-Warning "Pull failed. Skipping stash restoration until conflicts are fixed manually."
    }
}

function Do-Push {
    Write-Host "
[+] Preparing to push changes..." -ForegroundColor Cyan
    $commitMsg = Read-Host "Enter a short description of your changes (or press Enter for 'Update SOC Platform files')"
    if ([string]::IsNullOrWhiteSpace($commitMsg)) { 
        $commitMsg = "Update SOC Platform files" 
    }
    
    git add .
    git commit -m "$commitMsg"
    git push origin main
    Write-Host "✓ Successfully pushed to central repo!" -ForegroundColor Green
}

# --- Main Menu Loop ---
while ($true) {
    Write-Host "
=================================" -ForegroundColor Cyan
    Write-Host "       SOC DOCKER GIT SYNC       " -ForegroundColor Cyan
    Write-Host "=================================" -ForegroundColor Cyan
    
    Write-Host "
--- Current Local Status ---" -ForegroundColor Yellow
    git status -s
    Write-Host "----------------------------
" -ForegroundColor Yellow

    Write-Host "1. Push (Upload local changes)"
    Write-Host "2. Pull & Merge (Download updates)"
    Write-Host "3. Full Sync (Pull, Merge, then Push)"
    Write-Host "4. Exit"
    Write-Host "=================================" -ForegroundColor Cyan

    $choice = Read-Host "Select an option (1-4)"

    switch ($choice) {
        '1' { Do-Push }
        '2' { Do-Pull }
        '3' { 
            Do-Pull
            if ($LASTEXITCODE -eq 0) { Do-Push }
        }
        '4' { exit }
        default { Write-Warning "Invalid selection. Please choose 1-4." }
    }
}
