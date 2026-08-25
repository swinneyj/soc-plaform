param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$StopOllama
)

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  Stopping SOC Platform"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"

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