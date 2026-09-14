param(
    [string]$Branch = "",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$BaseDbPort = 9300,
    [int]$PortRange = 300
)

Set-Location $RepoRoot

if (-not $Branch -or -not $Branch.Trim()) {
    try {
        $Branch = (git rev-parse --abbrev-ref HEAD).Trim()
    } catch {
        Write-Error "Could not determine current Git branch. Provide -Branch explicitly."
        exit 1
    }
}

[int]$hash = [Math]::Abs($Branch.GetHashCode())
[int]$dbPort = $BaseDbPort + ($hash % $PortRange)

$databaseUrl = "postgresql+psycopg://soc_platform@localhost:$dbPort/soc_platform"

Write-Host "============================================"
Write-Host "  Sync shared logic for branch preview"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"
Write-Host "[*] Branch: $Branch"
Write-Host "[*] Computed DB Port: $dbPort"
Write-Host "[*] DATABASE_URL used for sync: $databaseUrl"

& (Join-Path $RepoRoot "scripts\sync_shared_logic_to_db.ps1") -DatabaseUrl $databaseUrl

if ($LASTEXITCODE -ne 0) {
    Write-Error "sync_shared_logic_to_db.ps1 failed for branch '$Branch' at $databaseUrl"
    exit $LASTEXITCODE
}

Write-Host "[+] Shared logic sync complete for branch '$Branch'"