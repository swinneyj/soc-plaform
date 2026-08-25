param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [switch]$StopOllama,
    [switch]$SkipSharedDumpExport
)

$script:LockFilePath = Join-Path $SharedDumpDir "db_in_use.lock.json"

function Read-LockFile {
    if (-not (Test-Path $script:LockFilePath)) {
        return $null
    }
    try {
        return Get-Content $script:LockFilePath -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Release-ShareLock {
    if (-not (Test-Path $script:LockFilePath)) {
        return
    }
    $existingLock = Read-LockFile
    if ($existingLock -and $existingLock.username -eq $env:USERNAME -and $existingLock.computer -eq $env:COMPUTERNAME) {
        Remove-Item $script:LockFilePath -Force
        Write-Host "[+] Share lock released"
    }
}

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  Stopping SOC Platform"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"

$exportSucceeded = $true

if (-not $SkipSharedDumpExport) {
    Write-Host "[*] Exporting current database state to shared dump..."
    & (Join-Path $PSScriptRoot "export_db_dump_to_share.ps1") -ShareDir $SharedDumpDir -DatabaseUrl $env:DATABASE_URL
    if ($LASTEXITCODE -ne 0) {
        $exportSucceeded = $false
        Write-Warning "Shared dump export failed. Containers will still be stopped, but the share lock will be retained to indicate an unsynced state."
    }
}

docker compose down
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose down failed."
    exit $LASTEXITCODE
}

if ($exportSucceeded -or $SkipSharedDumpExport) {
    Release-ShareLock
}

if ($StopOllama) {
    $ollamaProcesses = Get-Process -Name ollama -ErrorAction SilentlyContinue
    if ($ollamaProcesses) {
        Write-Host "[*] Stopping Ollama process(es)..."
        $ollamaProcesses | Stop-Process -Force
        Write-Host "[+] Ollama stopped"
    } else {
        Write-Host "[*] Ollama was not running"
    }
}

Write-Host "[+] SOC Platform containers stopped"