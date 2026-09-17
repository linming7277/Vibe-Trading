$repo = "D:\AI\hzstock"
function Stop-ProcessTree([int]$ProcessId) {
  $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
  foreach ($child in $children) { Stop-ProcessTree ([int]$child.ProcessId) }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}
$port_owner = Get-NetTCPConnection -State Listen -LocalPort 8899 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($port_owner) {
  Stop-ProcessTree ([int]$port_owner.OwningProcess)
  Start-Sleep -Seconds 2
}
$host_ = Start-Process -FilePath "powershell.exe" `
  -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",('"'+$repo+'\launcher\windows\ServiceHost.ps1"'),"-Service","backend","-RepoRoot",('"'+$repo+'"')) `
  -WorkingDirectory $repo -WindowStyle Hidden `
  -RedirectStandardOutput "$repo\.launcher\logs\backend.log" `
  -RedirectStandardError "$repo\.launcher\logs\backend-error.log" -PassThru
@{ pid = $host_.Id; service = "backend"; started_at = [DateTime]::UtcNow.ToString("o"); repo = $repo } |
  ConvertTo-Json | Set-Content -LiteralPath "$repo\.launcher\backend.json" -Encoding UTF8
"NEW HOST PID=$($host_.Id)"
