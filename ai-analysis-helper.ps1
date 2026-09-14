function Invoke-SocAiAnalysis {
    param(
        [Parameter(Mandatory)]
        [string]$CaseId,

        [string]$Model = "llama2",

        [string]$Context = "",

        [string]$BaseUrl = "http://127.0.0.1:8000"
    )

    $body = @{ case_id = $CaseId; model = $Model; context = $Context } | ConvertTo-Json

    try {
        $result = Invoke-RestMethod \
            -Uri "$BaseUrl/api/db/analyze" \
            -Method Post \
            -ContentType "application/json" \
            -Body $body

        return $result
    }
    catch {
        Write-Error "Invoke-SocAiAnalysis failed: $($_.Exception.Message)"
        throw
    }
}

<#!
Usage examples:

    # List triage cases
    $cases = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/db/triage?limit=50" -Method Get
    $cases | Select-Object case_id, rule_name, verdict | Format-Table

    # Run AI analysis for a case
    $result = Invoke-SocAiAnalysis -CaseId "CASE-12345" -Model "llama2" -Context "Testing via PowerShell"
    $result.analysis          # full narrative
    $result.phase2_queries    # machine-readable Phase 2 SPL recommendations

To make this function available in any PowerShell session, dot-source the script:

    . "C:\Users\justin.swinney\Downloads\SOC_Automation_Working\ai-analysis-helper.ps1"

or copy the function into your PowerShell profile.
#!>