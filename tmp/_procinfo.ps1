$p = Get-NetTCPConnection -State Listen -LocalPort 8899 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($p) {
  $proc = Get-Process -Id $p.OwningProcess
  "PORT OWNER PID=$($p.OwningProcess) START=$($proc.StartTime)"
} else { "NO LISTENER" }
