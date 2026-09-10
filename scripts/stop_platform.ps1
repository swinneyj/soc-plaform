param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [int]$PostgresHostPort = 5433,
    [switch]$StopOllama,
    [switch]$SkipSharedDumpExport,
    [switch]$IgnoreExistingLock
)

$script:LockFilePath = Join-Path $SharedDumpDir "db_in_use.lock.json"
$LockStaleHours = 8

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
        $isStale = $false
        if ($existingLock.PSObject.Properties.Name -contains 'timestamp' -and $existingLock.timestamp) {
            try {
                $lockTime = [DateTime]::Parse($existingLock.timestamp)
                $age = (Get-Date) - $lockTime
                if ($age.TotalHours -ge $LockStaleHours) {
                    $isStale = $true
                }
            } catch {
                # If we can't parse the timestamp, fall back to the original strict behavior.
            }
        }

        if (-not $sameOwner -and -not $isStale -and -not $IgnoreExistingLock) {
            Write-Error "A shared DB lock already exists for user '$($existingLock.username)' on '$($existingLock.computer)' since $($existingLock.timestamp). Use -IgnoreExistingLock only if you intentionally want to take over."
            return $false
        }
        if ($sameOwner) {
            Write-Host "[*] Reusing existing lock owned by this user/machine"
        } elseif ($isStale -and -not $sameOwner) {
            Write-Warning "Existing lock from user '$($existingLock.username)' on '$($existingLock.computer)' at $($existingLock.timestamp) is older than $LockStaleHours hour(s). Treating as stale and taking over."
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

if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:$PostgresHostPort/soc_platform"
}

Write-Host "============================================"
Write-Host "  Stopping SOC Platform"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"

$exportSucceeded = $true

try {
    $exportSharedLogicScript = Join-Path $PSScriptRoot "export_shared_logic_from_db.py"
    if (Test-Path $exportSharedLogicScript) {
        Write-Host "[*] Exporting shared rule/supportive logic snapshot from database..."
        python $exportSharedLogicScript
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Shared logic export script exited with non-zero code $LASTEXITCODE. Continuing with shutdown."
        } else {
            Write-Host "[+] Shared logic export complete."
        }
    } else {
        Write-Host "[*] Shared logic export script not found; skipping export."
    }
} catch {
    Write-Warning "Shared logic export failed: $($_.Exception.Message)"
}

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