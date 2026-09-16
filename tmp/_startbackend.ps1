$repo = "D:\AI\hzstock"
$host_ = Start-Process -FilePath "powershell.exe" `
  -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",('"'+$repo+'\launcher\windows\ServiceHost.ps1"'),"-Service","backend","-RepoRoot",('"'+$repo+'"')) `
  -WorkingDirectory $repo -WindowStyle Hidden `
  -RedirectStandardOutput "$repo\.launcher\logs\backend.log" `
  -RedirectStandardError "$repo\.launcher\logs\backend-error.log" -PassThru
@{ pid = $host_.Id; service = "backend"; started_at = [DateTime]::UtcNow.ToString("o"); repo = $repo } |
  ConvertTo-Json | Set-Content -LiteralPath "$repo\.launcher\backend.json" -Encoding UTF8
"HOST PID=$($host_.Id)"
