# make-updates-zip.ps1

$zipName = "soc-platform-updates-$((Get-Date).ToString('yyyyMMdd_HHmmss')).zip"

$files = @(
    "db\models.py",
    "scripts\migrate_sqlite_to_postgres.py",
    "inspect_db.py",
    "scripts\stop_platform.ps1"
)

Compress-Archive -Path $files -DestinationPath $zipName -Force

Write-Host "[+] Created update zip: $zipName"
Write-Host "    Contains:"
$files | ForEach-Object { Write-Host "     - $_" }