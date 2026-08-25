param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [switch]$StopOllama,
    [switch]$SkipSharedDumpExport,
    [switch]$IgnoreExistingLock
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

function Acquire-ShareLock {
    if (-not (Test-Path $SharedDumpDir)) {
        New-Item -ItemType Directory -Path $SharedDumpDir -Force | Out-Null
    }

    $existingLock = Read-LockFile
    if ($existingLock) {
        $sameOwner = ($existingLock.username -eq $env:USERNAME) -and ($existingLock.computer -eq $env:COMPUTERNAME)
        if (-not $sameOwner -and -not $IgnoreExistingLock) {
            Write-Error "A shared DB lock already exists for user '$($existingLock.username)' on '$($existingLock.computer)' since $($existingLock.timestamp). Use -IgnoreExistingLock only if you intentionally want to take over."
            return $false
        }
        if ($sameOwner) {
            Write-Host "[*] Reusing existing lock owned by this user/machine"
        } elseif ($IgnoreExistingLock) {
            Write-Warning "Overriding existing lock from another user/machine"
        }
    }

    $payload = @{
        username = $env:USERNAME
        computer = $env:COMPUTERNAME
        repo_root = $RepoRoot
        timestamp = (Get-Date).ToString("o")
        operation = "shared_dump_export"
    } | ConvertTo-Json

    Set-Content -Path $script:LockFilePath -Value $payload -Encoding UTF8
    Write-Host "[+] Share lock acquired: $script:LockFilePath"
    return $true
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
    if (-not (Acquire-ShareLock)) {
        exit 1
    }

    Write-Host "[*] Exporting current database state to shared dump..."
    & (Join-Path $PSScriptRoot "export_db_dump_to_share.ps1") -ShareDir $SharedDumpDir -DatabaseUrl $env:DATABASE_URL
    $exportExitCode = $LASTEXITCODE
    Release-ShareLock
    if ($exportExitCode -ne 0) {
        $exportSucceeded = $false
        Write-Warning "Shared dump export failed. Containers will still be stopped."
    }
}

docker compose down
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose down failed."
    exit $LASTEXITCODE
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