param(
    [int]$ApiPort = 8000,
    [int]$PostgresHostPort = 5433
)

function Test-Http {
    param([string]$Url)
    try {
        $result = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return @{ ok = $true; body = $result }
    } catch {
        return @{ ok = $false; body = $_.Exception.Message }
    }
}

Write-Host "============================================"
Write-Host "  SOC Platform Status"
Write-Host "============================================"

$dockerReady = $true
docker version *> $null
if ($LASTEXITCODE -ne 0) {
    $dockerReady = $false
}

Write-Host "Docker Ready: $dockerReady"

if ($dockerReady) {
    Write-Host ""
    Write-Host "Docker Compose Services:"
    docker compose ps
}

Write-Host ""
$apiHealth = Test-Http -Url "http://127.0.0.1:$ApiPort/health"
Write-Host "API /health: $($apiHealth.ok)"
if ($apiHealth.ok) {
    Write-Host ($apiHealth.body | ConvertTo-Json -Depth 3)
}

$apiCompat = Test-Http -Url "http://127.0.0.1:$ApiPort/api/health"
Write-Host "API /api/health: $($apiCompat.ok)"
if ($apiCompat.ok) {
    Write-Host ($apiCompat.body | ConvertTo-Json -Depth 3)
}

$ollamaHealth = Test-Http -Url "http://127.0.0.1:11434/api/tags"
Write-Host "Ollama: $($ollamaHealth.ok)"

$postgresPort = Get-NetTCPConnection -LocalPort $PostgresHostPort -State Listen -ErrorAction SilentlyContinue
Write-Host "PostgreSQL Port $PostgresHostPort Listening: $([bool]$postgresPort)"
if ($postgresPort) {
    $postgresPort | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize
}