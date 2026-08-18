param(
    [switch]$StartIfNeeded,
    [int]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$launcher = Join-Path $PSScriptRoot "HengzhiLauncher.ps1"
$backendUrl = "http://127.0.0.1:8899"
$frontendUrl = (& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -Action print-url | Select-Object -Last 1).Trim()
if (-not $frontendUrl) { $frontendUrl = "http://localhost:5899" }
$frontendRootUrl = $frontendUrl -replace '/value/?$', ''
$frontendCheckUrl = "http://localhost:5899"

function Invoke-Launcher([string]$Action) {
    $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -Action $Action
    if ($LASTEXITCODE -ne 0) { throw "Launcher action failed: $Action" }
    return ($raw -join [Environment]::NewLine) | ConvertFrom-Json
}

function Invoke-Api([string]$Path) {
    $client = New-Object System.Net.WebClient
    try {
        $bytes = $client.DownloadData($backendUrl + $Path)
        return ([System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
    }
    finally {
        $client.Dispose()
    }
}

$states = @()
foreach ($state in (Invoke-Launcher "status")) { $states += $state }
$notRunning = @($states | Where-Object { $_.Status -notin @("running", "workspace") })
if ($notRunning.Count -gt 0) {
    if (-not $StartIfNeeded) {
        throw "Services are not running. Start them first or pass -StartIfNeeded."
    }
    $states = @()
    foreach ($state in (Invoke-Launcher "start")) { $states += $state }
}

$health = Invoke-Api "/health"
if ($health.status -ne "healthy") { throw "Backend health check failed." }

$tdx = Invoke-Api "/tdx/status"
if (-not $tdx.available) { throw "TDX bridge is unavailable: $($tdx.tdx_home)" }
$quoteModule = @($tdx.modules | Where-Object { $_.code -eq "quote" })[0]
$fundamentalModule = @($tdx.modules | Where-Object { $_.code -eq "fundamental" })[0]
if (-not $quoteModule -or $quoteModule.item_count -lt 1) { throw "Quote cache is empty." }
if (-not $fundamentalModule -or $fundamentalModule.status -ne "ready" -or $fundamentalModule.item_count -lt 1) {
    throw "Fundamental cache is not ready."
}

$quotes = Invoke-Api "/tdx/data/quotes?limit=1"
$fundamentals = Invoke-Api "/tdx/data/fundamentals?limit=1"
if ($quotes.total -lt 1 -or @($quotes.items).Count -lt 1) { throw "Quote data API returned no records." }
if ($fundamentals.total -lt 1 -or @($fundamentals.items).Count -lt 1) { throw "Fundamental data API returned no records." }

$page = Invoke-WebRequest -Uri ($frontendCheckUrl + "/data") -UseBasicParsing -TimeoutSec $TimeoutSeconds
if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<div id="root">') {
    throw "Frontend data route did not return a valid SPA page."
}

$result = [ordered]@{
    status = "passed"
    checked_at = [DateTime]::UtcNow.ToString("o")
    launcher = @($states | ForEach-Object { [ordered]@{ service = $_.Service; status = $_.Status; pid = $_.Pid } })
    backend = [ordered]@{ endpoint = "internal:8899"; status = $health.status; service = $health.service }
    tdx = [ordered]@{
        home = $tdx.tdx_home
        quotes = [int]$quoteModule.item_count
        fundamentals = [int]$fundamentalModule.item_count
        fundamental_status = $fundamentalModule.status
    }
    data_api = [ordered]@{ quote_total = [int]$quotes.total; fundamental_total = [int]$fundamentals.total }
    frontend = [ordered]@{ url = ($frontendRootUrl + "/data"); status_code = [int]$page.StatusCode; spa_root = $true }
}
$result | ConvertTo-Json -Depth 6
