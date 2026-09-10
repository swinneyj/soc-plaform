param(
    [string]$ShareDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [string]$FileName = "current_soc_platform_dump.sql",
    [string]$DatabaseUrl = $env:DATABASE_URL
)

$outputPath = Join-Path $ShareDir $FileName
$metadataPath = Join-Path $ShareDir "current_soc_platform_dump.metadata.json"
$tempOutputPath = "$outputPath.tmp"

if (-not (Test-Path $ShareDir)) {
    New-Item -ItemType Directory -Path $ShareDir -Force | Out-Null
}

if (Test-Path $tempOutputPath) {
    Remove-Item $tempOutputPath -Force
}

& (Join-Path $PSScriptRoot "export_postgres_dump.ps1") -DatabaseUrl $DatabaseUrl -OutputPath $tempOutputPath

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$tempDumpFile = Get-Item $tempOutputPath
if ($tempDumpFile.Length -le 0) {
    Remove-Item $tempOutputPath -Force -ErrorAction SilentlyContinue
    Write-Error "Exported dump is empty. Shared dump was not updated."
    exit 1
}

Move-Item -Path $tempOutputPath -Destination $outputPath -Force

$dumpFile = Get-Item $outputPath
$metadata = @{
    file_name = $dumpFile.Name
    full_path = $dumpFile.FullName
    size_bytes = $dumpFile.Length
    exported_at = (Get-Date).ToString("o")
    exported_by_user = $env:USERNAME
    exported_by_computer = $env:COMPUTERNAME
    database_url = $DatabaseUrl
} | ConvertTo-Json

Set-Content -Path $metadataPath -Value $metadata -Encoding UTF8

Write-Host "[+] Shared dump updated: $outputPath"
Write-Host "[+] Shared dump metadata updated: $metadataPath"