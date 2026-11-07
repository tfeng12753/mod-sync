# Read-only lookup: https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key
# Uses the current shell's key or a hidden prompt. Never prints or saves the key.
param([switch]$NoPrompt)

$usageApiKey = $env:OPENROUTER_API_KEY
$usageSecret = $null
$usageHeaders = $null

try {
    if ([string]::IsNullOrWhiteSpace($usageApiKey)) {
        if ($NoPrompt) {
            throw 'OPENROUTER_API_KEY is not available in this PowerShell session. Run in the terminal where you set it, or omit -NoPrompt for a hidden key prompt.'
        }
        $usageSecret = Read-Host 'OpenRouter API key (hidden)' -AsSecureString
        $usageApiKey = [System.Net.NetworkCredential]::new('', $usageSecret).Password
    }
    if ([string]::IsNullOrWhiteSpace($usageApiKey)) {
        throw 'No API key supplied.'
    }

    $usageHeaders = @{ Authorization = "Bearer $($usageApiKey.Trim())" }
    try {
        $usageResponse = Invoke-RestMethod -Uri 'https://openrouter.ai/api/v1/key' `
            -Method Get -Headers $usageHeaders -TimeoutSec 20 -MaximumRedirection 0 -ErrorAction Stop
    } catch {
        $usageStatus = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { $null }
        if ($usageStatus -eq 401) {
            throw 'OpenRouter rejected this API key (HTTP 401). Check that you copied the full key.'
        }
        if ($usageStatus) { throw "OpenRouter usage lookup failed (HTTP $usageStatus)." }
        throw 'Could not reach OpenRouter. Check your internet connection and try again.'
    }

    if ($null -eq $usageResponse.data) { throw 'OpenRouter did not return key usage data.' }
    $usageData = $usageResponse.data
    [pscustomobject]@{
        total_spent_usd = $usageData.usage
        spent_today_usd = $usageData.usage_daily
        spent_this_week_usd = $usageData.usage_weekly
        spent_this_month_usd = $usageData.usage_monthly
        key_spending_limit_usd = $usageData.limit
        key_limit_remaining_usd = $usageData.limit_remaining
        limit_reset = $usageData.limit_reset
        is_free_tier = $usageData.is_free_tier
        expires_at = $usageData.expires_at
    } | Format-List
    if ($null -eq $usageData.limit) {
        Write-Output 'No spending limit is set on this key. This does not mean the account has unlimited credits.'
    }
    Write-Output 'These are key-level usage and limits, not the account credit balance. All monetary amounts are USD.'
} finally {
    if ($usageHeaders) { $usageHeaders.Clear() }
    $usageApiKey = $null
    if ($usageSecret) { $usageSecret.Dispose() }
}
