param (
    [string]$Message = "Automated updates"
)

# 1. Stage all changes
Write-Host "Staging changes..." -ForegroundColor Cyan
git add .

# 2. Commit changes
Write-Host "Committing changes..." -ForegroundColor Cyan
git commit -m $Message

# 3. Pull latest updates to prevent conflicts
Write-Host "Pulling latest changes..." -ForegroundColor Cyan
git pull origin main

# 4. If the pull was clean, push changes
if ($LASTEXITCODE -eq 0) {
    Write-Host "Pushing changes to remote..." -ForegroundColor Green
    git push origin main
} else {
    Write-Warning "Pull encountered conflicts or errors. Push aborted. Please resolve conflicts manually."
}