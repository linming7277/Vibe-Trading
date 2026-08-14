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
