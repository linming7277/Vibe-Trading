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

# Keep mainland official data direct even when this desktop session uses an
# HTTP(S) proxy for Codex/OpenAI.  This covers SDKs (Tushare/AKShare) that own
# their HTTP transports; a transparent TUN proxy still needs the Clash/V2Ray
# rules shipped beside this launcher.
$domesticNoProxyHosts = @(
    "gov.cn", "ndrc.gov.cn", "miit.gov.cn", "pbc.gov.cn", "safe.gov.cn",
    "chinamoney.com.cn", "shibor.org", "tushare.pro"
)
$existingNoProxy = @($env:NO_PROXY, $env:no_proxy) |
    Where-Object { $_ } |
    ForEach-Object { $_ -split "," } |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }
$mergedNoProxy = @($existingNoProxy + $domesticNoProxyHosts | Select-Object -Unique) -join ","
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
Set-Location -LiteralPath $frontendRoot
& $npm run dev -- --host 127.0.0.1 --port 5899 --strictPort
exit $LASTEXITCODE
