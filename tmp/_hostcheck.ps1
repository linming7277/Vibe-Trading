Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'ServiceHost' } | ForEach-Object {
  "HOST PID=$($_.ProcessId) START=$($_.CreationDate)"
}
