$state = "UNKNOWN"
foreach ($p in @("$env:USERPROFILE\.hengzhi-launcher", "$env:LOCALAPPDATA\hengzhi-launcher", "$env:TEMP\hengzhi-launcher")) {
  if (Test-Path $p) { $state = $p; break }
}
"STATE ROOT: $state"
if ($state -ne "UNKNOWN") { Get-ChildItem $state -Recurse -File | Select-Object -First 10 -ExpandProperty FullName }
