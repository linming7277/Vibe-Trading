Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn|api_server|serve' -and $_.Name -match 'python' } | ForEach-Object {
  "PID=$($_.ProcessId) START=$($_.CreationDate) CMD=$($_.CommandLine.Substring(0,[Math]::Min(140,$_.CommandLine.Length)))"
}
