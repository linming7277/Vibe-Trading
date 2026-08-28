param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("backend", "frontend")]
    [string]$Service,
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
$repo = [System.IO.Path]::GetFullPath($RepoRoot)
Set-Location -LiteralPath $repo

# Only the local application endpoints are forced out of the proxy. External
# market/financial providers intentionally keep the user's proxy route: on
# many networks Eastmoney/Tushare are reachable through the proxy but not by a
# direct TLS connection. Provider-specific direct clients can still opt out
# explicitly with trust_env=False.
$localNoProxyHosts = @("localhost", "127.0.0.1", "::1", "hzstock", "192.168.*")
$existingNoProxy = @($env:NO_PROXY, $env:no_proxy) |
    Where-Object { $_ } |
    ForEach-Object { $_ -split "," } |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }
$mergedNoProxy = @($existingNoProxy + $localNoProxyHosts | Select-Object -Unique) -join ","
$env:NO_PROXY = $mergedNoProxy
$env:no_proxy = $mergedNoProxy

if ($Service -eq "backend") {
    $backendExe = Join-Path $repo ".venv\Scripts\vibe-trading.exe"
    if (-not (Test-Path -LiteralPath $backendExe)) {
        throw "Backend executable not found: $backendExe"
    }
    & $backendExe serve --port 8899
    exit $LASTEXITCODE
}

$frontendRoot = Join-Path $repo "frontend"
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
    throw "Frontend dependencies are missing. Run npm install in $frontendRoot"
}
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$env:VITE_API_URL = "http://127.0.0.1:8899"
$machineHost = [System.Net.Dns]::GetHostName()
$configuredHost = if ([string]::IsNullOrWhiteSpace($env:HENGZHI_HOSTNAME)) { "127.0.0.1" } else { $env:HENGZHI_HOSTNAME.Trim() }
$env:VITE_ALLOWED_HOST = @($configuredHost, $machineHost) -join ","
Set-Location -LiteralPath $frontendRoot
& $npm run dev -- --host 0.0.0.0 --port 5899 --strictPort
exit $LASTEXITCODE
