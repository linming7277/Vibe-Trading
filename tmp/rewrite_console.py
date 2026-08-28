# -*- coding: utf-8 -*-
"""Rewrite Show-ConsoleWindow: flicker-free in-place render + client IP list."""
import io

path = r"D:\AI\hzstock\launcher\windows\HengzhiLauncher.ps1"
s = io.open(path, encoding="utf-8-sig").read()

helper = '''function Format-ConsoleStatusText([string]$Name, [string]$StatusText, [string]$Port, [string]$PidText) {
    # 中文按显示宽度 2 补齐，保持三列纵向对齐；返回纯文本行。
    $nameWidth = 0
    foreach ($char in $Name.ToCharArray()) { $nameWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
    $namePad = " " * [Math]::Max(1, 22 - $nameWidth)
    $statusWidth = 0
    foreach ($char in $StatusText.ToCharArray()) { $statusWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
    $statusPad = " " * [Math]::Max(1, 10 - $statusWidth)
    return "   " + $Name + $namePad + $StatusText + $statusPad + $Port + "    " + $PidText
}

'''

new_func = '''function Show-ConsoleWindow {
    <#
    Plain console monitor: one black window that keeps showing service
    states and stays open while the stack runs.  Services are independent
    hidden processes, so closing this window never stops them.  Frames are
    redrawn in place (cursor home + hidden cursor, no Clear-Host) so the
    picture does not flicker.
    #>
    Initialize-LauncherState
    try { $host.UI.RawUI.WindowTitle = "恒值投资 · 服务监视器" } catch {}
    if ($AutoStart) {
        try {
            Invoke-All "restart"
            Start-Process $script:FrontendUrl
        }
        catch {
            Write-Host ""
            Write-Host ("  启动失败: " + $_.Exception.Message) -ForegroundColor Red
        }
    }
    $lastLineCount = 0
    $inPlace = $true
    try { [Console]::CursorVisible = $false } catch { $inPlace = $false }
    try { [Console]::Clear() } catch {}

    while ($true) {
        $backend = Get-ServiceState "backend"
        $frontend = Get-ServiceState "frontend"
        $gateways = Get-HermesGatewayStates
        $clients = @(Get-RecentClientAccess)

        $lines = [System.Collections.Generic.List[string]]::new()
        $colored = [System.Collections.Generic.List[object]]::new()
        $lines.Add("")
        $lines.Add("  ══════════════════════════════════════════════════════════")
        $lines.Add("   恒值投资 · 服务监视器              $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
        $lines.Add("  ══════════════════════════════════════════════════════════")
        $lines.Add("")
        foreach ($state in @($backend, $frontend)) {
            $displayName = if ($state.Service -eq "backend") { "后端 API" } else { "前端 Web" }
            $statusText = "○ 已停止"
            $color = [ConsoleColor]::DarkGray
            if ($state.Status -in @("running", "workspace")) {
                $statusText = "● 运行中"
                $color = [ConsoleColor]::Green
            }
            elseif ($state.Status -eq "starting") {
                $statusText = "◐ 启动中"
                $color = [ConsoleColor]::Yellow
            }
            elseif ($state.Status -eq "conflict") {
                $statusText = "▲ 端口冲突"
                $color = [ConsoleColor]::Red
            }
            $pidText = if ($state.Pid -gt 0) { "PID $($state.Pid)" } else { "—" }
            $lines.Add((Format-ConsoleStatusText $displayName $statusText ([string]$state.Service.Port) $pidText))
            $colored.Add(@{ Line = $lines.Count - 1; Color = $color; Text = $statusText })
        }
        $lines.Add("   ──────────────────────────────────────────────────────")
        foreach ($gateway in $gateways) {
            $statusText = "○ 未运行"
            $color = [ConsoleColor]::DarkGray
            if ($gateway.Running) {
                $statusText = "● 运行中"
                $color = [ConsoleColor]::Green
            }
            $pidText = if ($gateway.Pid -gt 0) { "PID $($gateway.Pid)" } else { "—" }
            $lines.Add((Format-ConsoleStatusText ($gateway.Name + "网关") $statusText "—" $pidText))
            $colored.Add(@{ Line = $lines.Count - 1; Color = $color; Text = $statusText })
        }
        $lines.Add("")
        $lines.Add("   本机工作台:   " + $script:FrontendUrl)
        $lines.Add("   局域网访问:   " + $script:LanFrontendUrl)
        $lines.Add("   日志目录:     " + $script:LogRoot)
        $lines.Add("")
        $lines.Add("   最近访问设备（近 24 小时）")
        if ($clients.Count) {
            foreach ($client in ($clients | Select-Object -First 8)) {
                $ip = [string]$client.ip
                $displayIp = if ($ip -eq "127.0.0.1" -or $ip -eq "::1") { "$ip（本机）" } else { $ip }
                $lastSeen = Format-ClientAccessTime $client.last_seen
                $count = [string]$client.request_count
                $ipWidth = 0
                foreach ($char in $displayIp.ToCharArray()) { $ipWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
                $ipPad = " " * [Math]::Max(1, 28 - $ipWidth)
                $lines.Add("     " + $displayIp + $ipPad + "最后访问 " + $lastSeen + "    次数 " + $count)
            }
            if ($clients.Count -gt 8) {
                $lines.Add(("     …另有 {0} 台未列出" -f ($clients.Count - 8)))
            }
        }
        else {
            $lines.Add("     暂无记录；打开工作台后会自动记录。")
        }
        $lines.Add("")
        $lines.Add("  ──────────────────────────────────────────────────────────")
        $lines.Add("   [S]启动  [T]停止  [R]重启  [O]打开工作台  [L]日志目录  [Q]退出监视")
        $lines.Add("   直接关闭本窗口不会停止服务。")

        if ($inPlace) {
            try {
                $width = [Math]::Max(60, [Console]::WindowWidth - 1)
                [Console]::SetCursorPosition(0, 0)
                foreach ($line in $lines) {
                    [Console]::Out.Write($line.PadRight($width) + "\\r\\n")
                }
                for ($index = $lines.Count; $index -lt $lastLineCount; $index++) {
                    [Console]::Out.Write((" " * $width) + "\\r\\n")
                }
                $lastLineCount = $lines.Count
                foreach ($item in $colored) {
                    $lineIndex = [int]$item.Line
                    $text = [string]$item.Text
                    $color = $item.Color
                    $plain = $lines[$lineIndex]
                    $startCol = 3
                    foreach ($char in $plain.Substring(0, 3).ToCharArray()) { $startCol += if ([int]$char -gt 255) { 2 } else { 1 } }
                    $found = $plain.IndexOf($text)
                    if ($found -ge 0) {
                        $startCol = 0
                        foreach ($char in $plain.Substring(0, $found).ToCharArray()) { $startCol += if ([int]$char -gt 255) { 2 } else { 1 } }
                    }
                    [Console]::SetCursorPosition($startCol, $lineIndex)
                    [Console]::ForegroundColor = $color
                    [Console]::Out.Write($text)
                    [Console]::ResetColor()
                }
                [Console]::SetCursorPosition(0, 0)
            }
            catch {
                $inPlace = $false
                try { [Console]::CursorVisible = $true } catch {}
            }
        }
        if (-not $inPlace) {
            Clear-Host
            foreach ($line in $lines) { Write-Host $line }
        }

        $deadline = (Get-Date).AddSeconds(2)
        while ((Get-Date) -lt $deadline) {
            try {
                if (-not [Console]::KeyAvailable) {
                    Start-Sleep -Milliseconds 150
                    continue
                }
            }
            catch {
                Start-Sleep -Seconds 2
                break
            }
            $key = [Console]::ReadKey($true)
            $handled = $false
            switch ([char]::ToUpper($key.KeyChar)) {
                "S" { try { Invoke-All "start" } catch { Show-ConsoleError $_.Exception.Message }; $handled = $true }
                "T" { try { Invoke-All "stop" } catch { Show-ConsoleError $_.Exception.Message }; $handled = $true }
                "R" { try { Invoke-All "restart" } catch { Show-ConsoleError $_.Exception.Message }; $handled = $true }
                "O" { Start-Process $script:FrontendUrl }
                "L" { Start-Process explorer.exe -ArgumentList ('"' + $script:LogRoot + '"') }
                "Q" {
                    try { [Console]::CursorVisible = $true } catch {}
                    return
                }
            }
            if ($handled) { break }
        }
    }
}
'''

start = s.index("function Show-ConsoleWindow {")
end = s.index("function Show-ConsoleError")
s = s[:start] + helper + new_func + "\n" + s[end:]

io.open(path, "w", encoding="utf-8-sig").write(s)
print("rewritten OK")
