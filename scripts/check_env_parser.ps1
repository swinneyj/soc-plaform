#!/usr/bin/env pwsh
# Lightweight repeatable check for the .env parser regex shared by
# start_soc_api.ps1 and backup_db.ps1.
#
# Purpose: confirm the regex behaves as intended across representative
# inputs (comments, blanks, plain pairs, values with spaces, values
# containing '=', empty values, full DATABASE_URL) and that the expected
# key/value extractions match. This is a parser-level check only — it does
# not execute either script, touch the database, or require pg_dump/gzip.
#
# The regex used by both scripts:
#   '^\s*([^#=#\s][^=]*)=(.*)$'
#
# Run:  pwsh -File scripts/check_env_parser.ps1
#       .\scripts\check_env_parser.ps1
#
# Exit 0 = all assertions pass. Exit 1 = a case behaved unexpectedly.

$ErrorActionPreference = 'Stop'

$Regex = '^\s*([^#=#\s][^=]*)=(.*)$'

# Each entry: input, expected match?, expected key (if match), expected value (if match)
$Cases = @(
    @{ Input = '# full-line comment'          ; Match = $false },
    @{ Input = '   # indented comment'        ; Match = $false },
    @{ Input = ''                             ; Match = $false },
    @{ Input = '   '                          ; Match = $false },
    @{ Input = 'FOO=bar'                      ; Match = $true  ; Key = 'FOO';    Value = 'bar' },
    @{ Input = 'KEY=value with spaces'        ; Match = $true  ; Key = 'KEY';    Value = 'value with spaces' },
    @{ Input = 'KEY=value=with=equals'        ; Match = $true  ; Key = 'KEY';    Value = 'value=with=equals' },
    @{ Input = '  LEADINGSPACE=foo'           ; Match = $true  ; Key = 'LEADINGSPACE'; Value = 'foo' },
    @{ Input = 'TRAILINGSPACE =bar'           ; Match = $true  ; Key = 'TRAILINGSPACE'; Value = 'bar' },
    @{ Input = '#COMMENT=no-left-side'        ; Match = $false },
    @{ Input = '=noleftside'                  ; Match = $false },
    @{ Input = 'KEY=   '                      ; Match = $true  ; Key = 'KEY';    Value = '' },
    @{ Input = 'KEY='                         ; Match = $true  ; Key = 'KEY';    Value = '' },
    @{ Input = 'A=B'                          ; Match = $true  ; Key = 'A';      Value = 'B' },
    @{ Input = 'DATABASE_URL=postgresql://user:pass@host/db'
                                               ; Match = $true  ; Key = 'DATABASE_URL'; Value = 'postgresql://user:pass@host/db' },
    @{ Input = '  # not a key = value'        ; Match = $false },
    # Raw regex does no inline-comment stripping: value keeps the '# ...' tail
    @{ Input = 'KEY=value # inline comment'   ; Match = $true  ; Key = 'KEY';    Value = 'value # inline comment' }
)

$failed = 0
foreach ($c in $Cases) {
    $m = $null
    $didMatch = $c.Input -match $Regex
    if ($didMatch) { $m = $Matches }

    $gotMatch = [bool]$m
    if ($gotMatch -ne $c.Match) {
        Write-Host "FAIL: input=$([char]0x22)$($c.Input)$([char]0x22) — expected match=$($c.Match), got match=$gotMatch" -ForegroundColor Red
        $failed++
        continue
    }

    if ($gotMatch) {
        $gotKey = $m[1].Trim()
        $gotVal = $m[2].Trim()
        if ($gotKey -ne $c.Key) {
            Write-Host "FAIL: input=$([char]0x22)$($c.Input)$([char]0x22) — expected key=$([char]0x22)$($c.Key)$([char]0x22), got key=$([char]0x22)$gotKey$([char]0x22)" -ForegroundColor Red
            $failed++
            continue
        }
        if ($gotVal -ne $c.Value) {
            Write-Host "FAIL: input=$([char]0x22)$($c.Input)$([char]0x22) — expected value=$([char]0x22)$($c.Value)$([char]0x22), got value=$([char]0x22)$gotVal$([char]0x22)" -ForegroundColor Red
            $failed++
            continue
        }
        Write-Host "PASS: input=$([char]0x22)$($c.Input)$([char]0x22) -> $($gotKey)=$([char]0x22)$gotVal$([char]0x22)"
    } else {
        Write-Host "PASS: input=$([char]0x22)$($c.Input)$([char]0x22) -> rejected"
    }
}

if ($failed -gt 0) {
    Write-Host "RESULT: $($Failed) assertion(s) failed" -ForegroundColor Red
    exit 1
}

Write-Host "RESULT: all $($Cases.Count) cases passed"
exit 0
