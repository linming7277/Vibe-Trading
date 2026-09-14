# 恒值 · 实时访问日志窗口（2026-09-14）
# 只显示后端 HTTP 访问行：时间 / 来源 IP / 方法 / 页面路径 / 状态码。
# 数据源：.launcher\logs\backend.log（uvicorn access log），Get-Content -Wait 实时跟随。
param(
    [switch]$Once   # 一次性渲染现有日志尾部后退出（用于自检）
)

$ErrorActionPreference = "SilentlyContinue"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$logPath = Join-Path $repoRoot ".launcher\logs\backend.log"

$accessPattern = 'INFO:\s+(\S+:\d+)\s+-\s+"(\S+)\s+(\S+)[^"]*"\s+(\d{3})'

function Format-AccessLine([string]$raw) {
    if ($raw -match $accessPattern) {
        $ip = $matches[1] -replace ':\d+$', ''
        if ($ip -eq "127.0.0.1" -or $ip -eq "::1") { $ip = "$ip（本机）" }
        return @{
            method = $matches[2]; path = $matches[3]; status = $matches[4]; ip = $ip
        }
    }
    return $null
}

function Show-AccessEntry([hashtable]$entry) {
    $color = if ($entry.status -like "2*") { "Green" }
             elseif ($entry.status -like "3*") { "Cyan" }
             elseif ($entry.status -like "4*") { "Yellow" }
             else { "Red" }
    Write-Host ("[{0}] {1,-24} {2,-4} {3} " -f `
        (Get-Date -Format "HH:mm:ss"), $entry.ip, $entry.method, $entry.path) -NoNewline
    Write-Host $entry.status -ForegroundColor $color
}

Clear-Host
Write-Host "恒值投资 · 实时访问日志" -ForegroundColor White
Write-Host ("数据源: " + $logPath) -ForegroundColor DarkGray
Write-Host "只显示 HTTP 访问（IP / 页面 / 状态码）；按 Ctrl+C 退出。" -ForegroundColor DarkGray
Write-Host ("─" * 72) -ForegroundColor DarkGray

if ($Once) {
    Get-Content -Path $logPath -Tail 400 -Encoding UTF8 -ErrorAction SilentlyContinue |
        ForEach-Object { $entry = Format-AccessLine $_; if ($entry) { Show-AccessEntry $entry } }
    exit 0
}

Get-Content -Path $logPath -Wait -Tail 200 -Encoding UTF8 |
    ForEach-Object { $entry = Format-AccessLine $_; if ($entry) { Show-AccessEntry $entry } }
