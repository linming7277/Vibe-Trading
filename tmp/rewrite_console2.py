# -*- coding: utf-8 -*-
"""Rewrite console monitor: single-frame VT output, no cursor-column math."""
import io

path = r"D:\AI\hzstock\launcher\windows\HengzhiLauncher.ps1"
s = io.open(path, encoding="utf-8-sig").read()

new_block = r'''function Enable-ConsoleVirtualTerminal {
    # Windows 10+ conhost needs ENABLE_VIRTUAL_TERMINAL_PROCESSING before
    # ANSI escape sequences take effect; Windows Terminal already enables it.
    $signature = @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern IntPtr GetStdHandle(int nStdHandle);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out int lpMode);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleMode(IntPtr hConsoleHandle, int dwMode);
'@
    try {
        $api = Add-Type -MemberDefinition $signature -Name "ConsoleVT" -Namespace "Hengzhi.Launcher" -PassThru
        $handle = $api::GetStdHandle(-11)
        $mode = 0
        [void]$api::GetConsoleMode($handle, [ref]$mode)
        return $api::SetConsoleMode($handle, $mode -bor 0x0004)
    }
    catch { return $false }
}

function Format-ConsoleStatusText([string]$Name, [string]$StatusText, [string]$ColorCode, [string]$Port, [string]$PidText) {
    $esc = [char]27
    $nameWidth = 0
    foreach ($char in $Name.ToCharArray()) { $nameWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
    $namePad = " " * [Math]::Max(1, 22 - $nameWidth)
    $statusWidth = 0
    foreach ($char in $StatusText.ToCharArray()) { $statusWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
    $statusPad = " " * [Math]::Max(1, 10 - $statusWidth)
    return "   " + $Name + $namePad + "$esc[${ColorCode}m" + $StatusText + "$esc[0m" + $statusPad + $Port + "    " + $PidText
}

function Show-ConsoleWindow {
    <#
    Plain console monitor: one black window that keeps showing service
    states and stays open while the stack runs.  Services are independent
    hidden processes, so closing this window never stops them.  Each frame
    is one single write with inline ANSI colors and a cursor-home prefix,
    so nothing flickers and nothing overlaps.
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
    $esc = [char]27
    $vt = Enable-ConsoleVirtualTerminal
    if ($vt) { try { [Console]::Out.Write("$esc[?25l") } catch {} }
    try { [Console]::Clear() } catch {}
    $lastSize = ""

    while ($true) {
        $backend = Get-ServiceState "backend"
        $frontend = Get-ServiceState "frontend"
        $gateways = Get-HermesGatewayStates
        $clients = @(Get-RecentClientAccess)

        $lines = [System.Collections.Generic.List[string]]::new()
        $lines.Add("")
        $lines.Add("$esc[36m  ══════════════════════════════════════════════════════════$esc[0m")
        $lines.Add("   恒值投资 · 服务监视器              $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
        $lines.Add("$esc[36m  ══════════════════════════════════════════════════════════$esc[0m")
        $lines.Add("")
        foreach ($state in @($backend, $frontend)) {
            $displayName = if ($state.Service -eq "backend") { "后端 API" } else { "前端 Web" }
            $statusText = "○ 已停止"
            $colorCode = "90"
            if ($state.Status -in @("running", "workspace")) {
                $statusText = "● 运行中"
                $colorCode = "92"
            }
            elseif ($state.Status -eq "starting") {
                $statusText = "◐ 启动中"
                $colorCode = "93"
            }
            elseif ($state.Status -eq "conflict") {
                $statusText = "▲ 端口冲突"
                $colorCode = "91"
            }
            $pidText = if ($state.Pid -gt 0) { "PID $($state.Pid)" } else { "—" }
            $lines.Add((Format-ConsoleStatusText $displayName $statusText $colorCode ([string]$state.Service.Port) $pidText))
        }
        $lines.Add("$esc[90m   ──────────────────────────────────────────────────────$esc[0m")
        foreach ($gateway in $gateways) {
            $statusText = "○ 未运行"
            $colorCode = "90"
            if ($gateway.Running) {
                $statusText = "● 运行中"
                $colorCode = "92"
            }
            $pidText = if ($gateway.Pid -gt 0) { "PID $($gateway.Pid)" } else { "—" }
            $lines.Add((Format-ConsoleStatusText ($gateway.Name + "网关") $statusText $colorCode "—" $pidText))
        }
        $lines.Add("")
        $lines.Add("   本机工作台:   $esc[96m" + $script:FrontendUrl + "$esc[0m")
        $lines.Add("   局域网访问:   $esc[96m" + $script:LanFrontendUrl + "$esc[0m")
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
        $lines.Add("$esc[90m  ──────────────────────────────────────────────────────────$esc[0m")
        $lines.Add("   $esc[97m[S]启动  [T]停止  [R]重启  [O]打开工作台  [L]日志目录  [Q]退出监视$esc[0m")
        $lines.Add("$esc[90m   直接关闭本窗口不会停止服务。$esc[0m")

        if ($vt) {
            try {
                $size = [string][Console]::WindowWidth + "x" + [string][Console]::WindowHeight
                if ($size -ne $lastSize) {
                    [Console]::Clear()
                    $lastSize = $size
                }
                $width = [Math]::Max(60, [Console]::WindowWidth - 1)
                $escPattern = [string][char]27 + "\[[0-9;]*m"
                $frame = ($lines | ForEach-Object {
                    $visible = ($_ -replace $escPattern, "")
                    $_ + (" " * [Math]::Max(0, $width - $visible.Length))
                }) -join "`r`n"
                [Console]::Out.Write("$esc[H" + $frame)
            }
            catch { $vt = $false }
        }
        if (-not $vt) {
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
                    if ($vt) { try { [Console]::Out.Write("$esc[?25h$esc[0m") } catch {} }
                    return
                }
            }
            if ($handled) { break }
        }
    }
}
'''

start = s.index("function Format-ConsoleStatusText(")
end = s.index("function Show-ConsoleError")
s = s[:start] + new_block + "\n" + s[end:]

io.open(path, "w", encoding="utf-8-sig").write(s)
print("rewritten OK")
