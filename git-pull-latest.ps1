# 1. Temporarily save any local changes on Computer B
Write-Host "Stashing any local changes to prevent conflicts..." -ForegroundColor Cyan
git stash

# 2. Pull the latest changes from Computer A
Write-Host "Pulling latest updates from origin main..." -ForegroundColor Cyan
git pull origin main

# 3. Bring your local changes back on top of the new code
if ($LASTEXITCODE -eq 0) {
    # Check if there was actually anything stashed to bring back
    $hasStash = git stash list
    if ($hasStash) {
        Write-Host "Restoring your local changes..." -ForegroundColor Green
        git stash pop
    } else {
        Write-Host "✓ Successfully updated! No local changes to restore." -ForegroundColor Green
    }
} else {
    Write-Warning "Pull failed. Skipping stash restoration until conflicts are fixed manually."
}
