param(
    [ValidateSet("gui", "console", "start", "stop", "restart", "status", "smoke", "print-url", "install-shortcut")]
    [string]$Action = "console",
    [switch]$AutoStart
)

$ErrorActionPreference = "Stop"
$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$script:StateRoot = Join-Path $script:RepoRoot ".launcher"
$script:LogRoot = Join-Path $script:StateRoot "logs"
$script:ClientAccessFile = Join-Path $script:StateRoot "access-clients.json"
$script:HostScript = Join-Path $PSScriptRoot "ServiceHost.ps1"
$script:Strings = Get-Content -LiteralPath (Join-Path $PSScriptRoot "strings.zh-CN.json") -Raw -Encoding UTF8 | ConvertFrom-Json

function Get-PreferredLanAddress {
    try {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.AddressState -eq "Preferred" } |
            Select-Object -ExpandProperty IPAddress
        foreach ($address in $addresses) {
            if ($address -like "10.*" -or $address -like "192.168.*") { return $address }
            if ($address -match "^172\.(1[6-9]|2[0-9]|3[0-1])\.") { return $address }
        }
    }
    catch {}
    return ""
}

$script:LanHost = if ([string]::IsNullOrWhiteSpace($env:HENGZHI_HOSTNAME)) { "127.0.0.1" } else { $env:HENGZHI_HOSTNAME.Trim() }
$script:FrontendUrl = "http://$($script:LanHost):5899/value"
$script:LanAddress = Get-PreferredLanAddress
$script:LanFrontendUrl = if ($script:LanAddress) { "http://$($script:LanAddress):5899/value" } else { "未检测到可用局域网 IPv4 地址" }

function Ensure-LocalProxyBypass {
    <#
    Browsers and PowerShell honor the Windows per-user proxy override, while
    Python/Node mostly honor NO_PROXY. Keep both sides aligned so enabling a
    desktop proxy cannot turn the local LAN URL into a 502.
    #>
    $internetSettings = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try {
        $raw = (Get-ItemProperty -Path $internetSettings -Name ProxyOverride -ErrorAction SilentlyContinue).ProxyOverride
        $entries = @($raw -split ";" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        $required = @("localhost", "127.*", "::1", "hzstock", "192.168.*")
        $changed = $false
        foreach ($item in $required) {
            if ($entries -notcontains $item) { $entries += $item; $changed = $true }
        }
        if ($changed) {
            Set-ItemProperty -Path $internetSettings -Name ProxyOverride -Value ($entries -join ";") -Type String -ErrorAction Stop
        }
    }
    catch {
        # A locked-down corporate policy may reject user proxy edits. The
        # launcher still protects local Python/Node traffic through NO_PROXY.
    }
}

Ensure-LocalProxyBypass

$script:Services = @{
    backend = @{
        Name = "backend"
        Port = 8899
        PidFile = Join-Path $script:StateRoot "backend.json"
        OutLog = Join-Path $script:LogRoot "backend.log"
        ErrLog = Join-Path $script:LogRoot "backend-error.log"
    }
    frontend = @{
        Name = "frontend"
        Port = 5899
        PidFile = Join-Path $script:StateRoot "frontend.json"
        OutLog = Join-Path $script:LogRoot "frontend.log"
        ErrLog = Join-Path $script:LogRoot "frontend-error.log"
    }
}

function Initialize-LauncherState {
    New-Item -ItemType Directory -Path $script:LogRoot -Force | Out-Null
}

function Get-RecentClientAccess {
    if (-not (Test-Path -LiteralPath $script:ClientAccessFile)) { return @() }
    try {
        $payload = Get-Content -LiteralPath $script:ClientAccessFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $clients = @($payload.clients | Where-Object {
            $_ -and -not [string]::IsNullOrWhiteSpace([string]$_.ip) -and
            -not [string]::IsNullOrWhiteSpace([string]$_.last_seen)
        })
        return @($clients | Sort-Object -Property last_seen -Descending | Select-Object -First 50)
    }
    catch {
        return @()
    }
}

function Format-ClientAccessTime($value) {
    try { return ([DateTimeOffset]::Parse([string]$value)).ToLocalTime().ToString("MM-dd HH:mm:ss") }
    catch { return "—" }
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine
    }
    catch {
        return ""
    }
}

function Test-WorkspaceProcess([int]$ProcessId, [string]$ServiceName) {
    $commandLine = Get-ProcessCommandLine $ProcessId
    if (-not $commandLine) { return $false }
    if ($commandLine.IndexOf($script:RepoRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $false
    }
    if ($ServiceName -eq "backend") {
        return $commandLine -match "vibe-trading" -and $commandLine -match "serve"
    }
    return $commandLine -match "vite|npm" -and $commandLine -match "5899|frontend"
}

function Get-PortOwner([int]$Port) {
    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1
        if ($connection) { return [int]$connection.OwningProcess }
    }
    catch {}
    return 0
}

function Get-TrackedPid($Service) {
    if (-not (Test-Path -LiteralPath $Service.PidFile)) { return 0 }
    try {
        $state = Get-Content -LiteralPath $Service.PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $pidValue = [int]$state.pid
        if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) { return $pidValue }
    }
    catch {}
    Remove-Item -LiteralPath $Service.PidFile -Force -ErrorAction SilentlyContinue
    return 0
}

function Get-ServiceState([string]$ServiceName) {
    $service = $script:Services[$ServiceName]
    $trackedPid = Get-TrackedPid $service
    $portPid = Get-PortOwner $service.Port
    if ($portPid -gt 0) {
        if ($trackedPid -gt 0) {
            return [pscustomobject]@{ Service = $ServiceName; Status = "running"; Pid = $trackedPid; PortPid = $portPid }
        }
        if (Test-WorkspaceProcess $portPid $ServiceName) {
            return [pscustomobject]@{ Service = $ServiceName; Status = "workspace"; Pid = $portPid; PortPid = $portPid }
        }
        return [pscustomobject]@{ Service = $ServiceName; Status = "conflict"; Pid = $portPid; PortPid = $portPid }
    }
    if ($trackedPid -gt 0) {
        return [pscustomobject]@{ Service = $ServiceName; Status = "starting"; Pid = $trackedPid; PortPid = 0 }
    }
    return [pscustomobject]@{ Service = $ServiceName; Status = "stopped"; Pid = 0; PortPid = 0 }
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-LauncherService([string]$ServiceName) {
    $service = $script:Services[$ServiceName]
    $state = Get-ServiceState $ServiceName
    if ($state.Status -eq "conflict") {
        throw "Port $($service.Port) is used by another program (PID $($state.Pid))."
    }
    if ($state.Pid -gt 0) {
        Stop-ProcessTree $state.Pid
    }
    if ($state.PortPid -gt 0 -and $state.PortPid -ne $state.Pid) {
        Stop-ProcessTree $state.PortPid
    }
    Remove-Item -LiteralPath $service.PidFile -Force -ErrorAction SilentlyContinue
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $remaining = Get-PortOwner $service.Port
        if ($remaining -eq 0) { break }
        if (-not (Get-Process -Id $remaining -ErrorAction SilentlyContinue)) {
            Start-Sleep -Milliseconds 250
            continue
        }
        if (-not (Test-WorkspaceProcess $remaining $ServiceName)) {
            throw "Port $($service.Port) is still occupied by PID $remaining."
        }
        Stop-ProcessTree $remaining
        Start-Sleep -Milliseconds 250
    }
}

function Start-LauncherService([string]$ServiceName) {
    Initialize-LauncherState
    $service = $script:Services[$ServiceName]
    $state = Get-ServiceState $ServiceName
    if ($state.Status -in @("running", "workspace", "starting")) { return $state }
    if ($state.Status -eq "conflict") {
        throw "Port $($service.Port) is used by another program (PID $($state.Pid))."
    }
    if ($ServiceName -eq "backend") {
        $required = Join-Path $script:RepoRoot ".venv\Scripts\vibe-trading.exe"
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Backend is not installed. Missing: $required"
        }
    }
    else {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
            throw "npm.cmd is not available. Install Node.js first."
        }
        if (-not (Test-Path -LiteralPath (Join-Path $script:RepoRoot "frontend\node_modules"))) {
            throw "Frontend dependencies are missing. Run npm install in the frontend directory."
        }
    }
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $script:HostScript + '"'),
        "-Service", $ServiceName,
        "-RepoRoot", ('"' + $script:RepoRoot + '"')
    )
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $script:RepoRoot -WindowStyle Hidden -RedirectStandardOutput $service.OutLog -RedirectStandardError $service.ErrLog -PassThru
    @{ pid = $process.Id; service = $ServiceName; started_at = [DateTime]::UtcNow.ToString("o"); repo = $script:RepoRoot } |
        ConvertTo-Json | Set-Content -LiteralPath $service.PidFile -Encoding UTF8
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        $current = Get-ServiceState $ServiceName
        if ($current.Status -in @("running", "workspace")) { return $current }
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
            $errorTail = if (Test-Path -LiteralPath $service.ErrLog) { (Get-Content -LiteralPath $service.ErrLog -Tail 12 -ErrorAction SilentlyContinue) -join [Environment]::NewLine } else { "" }
            throw "Service exited before opening port $($service.Port). $errorTail"
        }
    }
    throw "Service did not open port $($service.Port) within 10 seconds."
}

function Restart-LauncherService([string]$ServiceName) {
    Stop-LauncherService $ServiceName
    return Start-LauncherService $ServiceName
}

function Invoke-All([string]$Operation) {
    if ($Operation -eq "start") {
        Write-Host "正在启动后端 (8899)..."
        Start-LauncherService "backend" | Out-Null
        Write-Host "正在启动前端 (5899)..."
        Start-LauncherService "frontend" | Out-Null
        Write-Host "前后端已启动。"
        return
    }
    if ($Operation -eq "stop") {
        Write-Host "正在停止前端..."
        Stop-LauncherService "frontend"
        Write-Host "正在停止后端..."
        Stop-LauncherService "backend"
        Write-Host "已停止。"
        return
    }
    Write-Host "正在停止前端..."
    Stop-LauncherService "frontend"
    Write-Host "正在停止后端..."
    Stop-LauncherService "backend"
    Write-Host "正在启动后端 (8899)..."
    Start-LauncherService "backend" | Out-Null
    Write-Host "正在启动前端 (5899)..."
    Start-LauncherService "frontend" | Out-Null
    Write-Host "前后端已重启完成。"
}

function Install-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop $script:Strings.shortcutName
    $target = Join-Path $script:RepoRoot "Hengzhi-Launcher.cmd"
    $icon = Join-Path $script:RepoRoot "assets\hz-icon.ico"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $script:RepoRoot
    $shortcut.Description = $script:Strings.title
    if (Test-Path -LiteralPath $icon) {
        $shortcut.IconLocation = $icon
    }
    else {
        $shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,105"
    }
    $shortcut.Save()
    Write-Host ($script:Strings.shortcutCreated + " " + $shortcutPath)
}

function Show-LauncherWindow {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    Initialize-LauncherState

    $form = New-Object System.Windows.Forms.Form
    $form.Text = $script:Strings.title
    $form.Size = New-Object System.Drawing.Size(760, 610)
    $form.MinimumSize = New-Object System.Drawing.Size(760, 610)
    $form.StartPosition = "CenterScreen"
    $form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
    $form.BackColor = [System.Drawing.Color]::FromArgb(246, 248, 252)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = $script:Strings.title
    $title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 18, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(28, 22)
    $form.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = $script:Strings.subtitle
    $subtitle.ForeColor = [System.Drawing.Color]::FromArgb(91, 103, 122)
    $subtitle.AutoSize = $true
    $subtitle.Location = New-Object System.Drawing.Point(31, 62)
    $form.Controls.Add($subtitle)

    $statusLabels = @{}
    function Add-ServiceRow([string]$serviceName, [int]$top, [string]$displayName, [string]$hint) {
        $panel = New-Object System.Windows.Forms.Panel
        $panel.Location = New-Object System.Drawing.Point(28, $top)
        $panel.Size = New-Object System.Drawing.Size(690, 82)
        $panel.BackColor = [System.Drawing.Color]::White
        $panel.BorderStyle = "FixedSingle"
        $form.Controls.Add($panel)

        $nameLabel = New-Object System.Windows.Forms.Label
        $nameLabel.Text = $displayName
        $nameLabel.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 11, [System.Drawing.FontStyle]::Bold)
        $nameLabel.Location = New-Object System.Drawing.Point(16, 13)
        $nameLabel.AutoSize = $true
        $panel.Controls.Add($nameLabel)

        $hintLabel = New-Object System.Windows.Forms.Label
        $hintLabel.Text = $hint
        $hintLabel.ForeColor = [System.Drawing.Color]::FromArgb(105, 115, 132)
        $hintLabel.Location = New-Object System.Drawing.Point(17, 44)
        $hintLabel.AutoSize = $true
        $panel.Controls.Add($hintLabel)

        $stateLabel = New-Object System.Windows.Forms.Label
        $stateLabel.Location = New-Object System.Drawing.Point(270, 29)
        $stateLabel.Size = New-Object System.Drawing.Size(105, 24)
        $stateLabel.TextAlign = "MiddleCenter"
        $panel.Controls.Add($stateLabel)
        $statusLabels[$serviceName] = $stateLabel

        $left = 390
        foreach ($definition in @(@($script:Strings.start, "start"), @($script:Strings.restart, "restart"), @($script:Strings.stop, "stop"))) {
            $button = New-Object System.Windows.Forms.Button
            $button.Text = $definition[0]
            $button.Tag = "$serviceName|$($definition[1])"
            $button.Location = New-Object System.Drawing.Point($left, 24)
            $button.Size = New-Object System.Drawing.Size(64, 32)
            $button.FlatStyle = "Flat"
            $button.Add_Click({
                $parts = [string]$this.Tag -split "\|"
                try {
                    if ($parts[1] -eq "start") { Start-LauncherService $parts[0] | Out-Null }
                    elseif ($parts[1] -eq "restart") { Restart-LauncherService $parts[0] | Out-Null }
                    else { Stop-LauncherService $parts[0] }
                    Update-StatusLabels
                }
                catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null }
            })
            $panel.Controls.Add($button)
            $left += 68
        }
    }

    Add-ServiceRow "backend" 92 $script:Strings.backend "前端代理后端（本机端口 8899）"
    Add-ServiceRow "frontend" 184 $script:Strings.frontend $script:FrontendUrl

    function Update-StatusLabels {
        foreach ($serviceName in @("backend", "frontend")) {
            $state = Get-ServiceState $serviceName
            $label = $statusLabels[$serviceName]
            if ($state.Status -eq "running") { $label.Text = $script:Strings.running; $label.ForeColor = [System.Drawing.Color]::FromArgb(20, 135, 84) }
            elseif ($state.Status -eq "workspace") { $label.Text = $script:Strings.managedElsewhere; $label.ForeColor = [System.Drawing.Color]::FromArgb(20, 105, 170) }
            elseif ($state.Status -eq "starting") { $label.Text = $script:Strings.starting; $label.ForeColor = [System.Drawing.Color]::FromArgb(183, 112, 0) }
            elseif ($state.Status -eq "conflict") { $label.Text = $script:Strings.conflict; $label.ForeColor = [System.Drawing.Color]::FromArgb(190, 45, 45) }
            else { $label.Text = $script:Strings.stopped; $label.ForeColor = [System.Drawing.Color]::FromArgb(105, 115, 132) }
        }
    }

    $allButtons = @(
        @($script:Strings.startAll, "start", 28),
        @($script:Strings.restartAll, "restart", 130),
        @($script:Strings.stopAll, "stop", 232)
    )
    foreach ($definition in $allButtons) {
        $button = New-Object System.Windows.Forms.Button
        $button.Text = $definition[0]
        $button.Tag = $definition[1]
        $button.Location = New-Object System.Drawing.Point([int]$definition[2], 286)
        $button.Size = New-Object System.Drawing.Size(94, 36)
        $button.FlatStyle = "Flat"
        $button.Add_Click({
            try { Invoke-All ([string]$this.Tag); Update-StatusLabels }
            catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null }
        })
        $form.Controls.Add($button)
    }

    $openButton = New-Object System.Windows.Forms.Button
    $openButton.Text = $script:Strings.openWorkbench
    $openButton.Location = New-Object System.Drawing.Point(486, 286)
    $openButton.Size = New-Object System.Drawing.Size(112, 36)
    $openButton.FlatStyle = "Flat"
    $openButton.Add_Click({ Start-Process $script:FrontendUrl })
    $form.Controls.Add($openButton)

    $logsButton = New-Object System.Windows.Forms.Button
    $logsButton.Text = $script:Strings.openLogs
    $logsButton.Location = New-Object System.Drawing.Point(606, 286)
    $logsButton.Size = New-Object System.Drawing.Size(112, 36)
    $logsButton.FlatStyle = "Flat"
    $logsButton.Add_Click({ Initialize-LauncherState; Start-Process explorer.exe -ArgumentList ('"' + $script:LogRoot + '"') })
    $form.Controls.Add($logsButton)

    $accessTitle = New-Object System.Windows.Forms.Label
    $accessTitle.Text = "最近访问设备（本机与局域网）"
    $accessTitle.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10, [System.Drawing.FontStyle]::Bold)
    $accessTitle.Location = New-Object System.Drawing.Point(31, 340)
    $accessTitle.AutoSize = $true
    $form.Controls.Add($accessTitle)

    $clientList = New-Object System.Windows.Forms.ListView
    $clientList.Location = New-Object System.Drawing.Point(28, 365)
    $clientList.Size = New-Object System.Drawing.Size(690, 145)
    $clientList.View = [System.Windows.Forms.View]::Details
    $clientList.FullRowSelect = $true
    $clientList.GridLines = $true
    $clientList.MultiSelect = $false
    [void]$clientList.Columns.Add("设备 IP", 220)
    [void]$clientList.Columns.Add("首次访问", 160)
    [void]$clientList.Columns.Add("最后访问", 160)
    [void]$clientList.Columns.Add("请求次数", 100)
    $form.Controls.Add($clientList)

    $accessHint = New-Object System.Windows.Forms.Label
    $accessHint.ForeColor = [System.Drawing.Color]::FromArgb(105, 115, 132)
    $accessHint.Location = New-Object System.Drawing.Point(31, 515)
    $accessHint.Size = New-Object System.Drawing.Size(680, 20)
    $form.Controls.Add($accessHint)

    $footer = New-Object System.Windows.Forms.Label
    $footer.Text = "本机：$($script:FrontendUrl)；同一局域网设备请使用：$($script:LanFrontendUrl)"
    $footer.ForeColor = [System.Drawing.Color]::FromArgb(105, 115, 132)
    $footer.Location = New-Object System.Drawing.Point(31, 540)
    $footer.Size = New-Object System.Drawing.Size(680, 30)
    $form.Controls.Add($footer)

    $lastClientSignature = ""
    function Update-ClientList {
        $clients = @(Get-RecentClientAccess)
        $signature = ($clients | ForEach-Object { "$($_.ip)|$($_.first_seen)|$($_.last_seen)|$($_.request_count)" }) -join "`n"
        if ($signature -eq $lastClientSignature) { return }
        $lastClientSignature = $signature
        $clientList.BeginUpdate()
        try {
            $clientList.Items.Clear()
            foreach ($client in $clients) {
                $ip = [string]$client.ip
                $displayIp = if ($ip -eq "127.0.0.1" -or $ip -eq "::1") { "$ip（本机）" } else { $ip }
                $item = New-Object System.Windows.Forms.ListViewItem($displayIp)
                [void]$item.SubItems.Add((Format-ClientAccessTime $client.first_seen))
                [void]$item.SubItems.Add((Format-ClientAccessTime $client.last_seen))
                [void]$item.SubItems.Add([string]$client.request_count)
                [void]$clientList.Items.Add($item)
            }
        }
        finally { $clientList.EndUpdate() }
        $accessHint.Text = if ($clients.Count) { "仅保留最近 24 小时连接；每个 IP 最多每 5 秒更新一次。" } else { "暂无访问记录；同事打开局域网地址后会自动显示在这里。" }
    }

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1500
    $timer.Add_Tick({ Update-StatusLabels; Update-ClientList })
    $timer.Start()
    Update-StatusLabels
    Update-ClientList
    if ($AutoStart) {
        $form.Add_Shown({
            try {
                Invoke-All "restart"
                Update-StatusLabels
                Start-Process $script:FrontendUrl
            }
            catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null }
        })
    }
    [void]$form.ShowDialog()
    $timer.Stop()
}

function Get-HermesGatewayStates {
    $profiles = @(
        @{ Key = "supervisor"; Name = "投研主管" },
        @{ Key = "financial";  Name = "财报研究员" },
        @{ Key = "risk";       Name = "风险研究员" },
        @{ Key = "valuation";  Name = "估值研究员" },
        @{ Key = "macro";      Name = "宏观研究员" }
    )
    try {
        $gateways = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop |
            Where-Object {
                $_.CommandLine -match "hermes_cli\.main" -and $_.CommandLine -match "gateway run"
            })
    }
    catch { $gateways = @() }
    $states = @()
    foreach ($profile in $profiles) {
        $match = $gateways |
            Where-Object { $_.CommandLine -match ("--profile\s+hzstock" + $profile.Key) } |
            Select-Object -First 1
        $states += [pscustomobject]@{
            Name = $profile.Name
            Key = $profile.Key
            Running = [bool]$match
            Pid = if ($match) { [int]$match.ProcessId } else { 0 }
        }
    }
    return , $states
}

function Format-ConsoleStatusRow([string]$Name, [string]$StatusText, [ConsoleColor]$Color, [string]$Port, [string]$PidText) {
    # 中文按显示宽度 2 补齐，保持三列在控制台里纵向对齐。
    $nameWidth = 0
    foreach ($char in $Name.ToCharArray()) { $nameWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
    $statusWidth = 0
    foreach ($char in $StatusText.ToCharArray()) { $statusWidth += if ([int]$char -gt 255) { 2 } else { 1 } }
    $namePad = " " * [Math]::Max(1, 22 - $nameWidth)
    $statusPad = " " * [Math]::Max(1, 10 - $statusWidth)
    Write-Host ("   " + $Name + $namePad) -NoNewline
    Write-Host $StatusText -ForegroundColor $Color -NoNewline
    Write-Host ($statusPad + $Port + "    " + $PidText)
}

function Enable-ConsoleVirtualTerminal {
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

function Show-ConsoleError([string]$Message) {
    Write-Host ""
    Write-Host ("  操作失败: " + $Message) -ForegroundColor Red
    Start-Sleep -Seconds 3
}

if ($Action -eq "install-shortcut") { Install-DesktopShortcut; exit 0 }
if ($Action -eq "print-url") { Write-Output $script:FrontendUrl; exit 0 }
if ($Action -eq "smoke") {
    & (Join-Path $PSScriptRoot "Smoke-Hengzhi-Stack.ps1") -StartIfNeeded
    exit $LASTEXITCODE
}
if ($Action -eq "status") {
    @((Get-ServiceState "backend"), (Get-ServiceState "frontend")) | ConvertTo-Json
    exit 0
}
if ($Action -in @("start", "stop", "restart")) {
    Invoke-All $Action
    @((Get-ServiceState "backend"), (Get-ServiceState "frontend")) | ConvertTo-Json
    exit 0
}
if ($Action -eq "console") { Show-ConsoleWindow; exit 0 }
Show-LauncherWindow
