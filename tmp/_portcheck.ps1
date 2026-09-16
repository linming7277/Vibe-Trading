$conn = Get-NetTCPConnection -State Listen -LocalPort 8899 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { "PORT 8899 OWNER PID=$($conn.OwningProcess)" } else { "PORT 8899 FREE" }
if (Test-Path "$env:USERPROFILE\.vibe-trading\launcher\backend.json") { "PIDFILE: " + (Get-Content "$env:USERPROFILE\.vibe-trading\launcher\backend.json" -Raw) } else { "no pidfile at ~/.vibe-trading/launcher" }
Get-ChildItem "$env:USERPROFILE\.vibe-trading\launcher" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
