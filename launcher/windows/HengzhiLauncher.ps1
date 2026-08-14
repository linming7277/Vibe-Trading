param(
    [ValidateSet("gui", "start", "stop", "restart", "status", "smoke", "install-shortcut")]
    [string]$Action = "gui"
)

$ErrorActionPreference = "Stop"
$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$script:StateRoot = Join-Path $script:RepoRoot ".launcher"
$script:LogRoot = Join-Path $script:StateRoot "logs"
$script:HostScript = Join-Path $PSScriptRoot "ServiceHost.ps1"
$script:Strings = Get-Content -LiteralPath (Join-Path $PSScriptRoot "strings.zh-CN.json") -Raw -Encoding UTF8 | ConvertFrom-Json

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
    Remove-Item -LiteralPath $service.PidFile -Force -ErrorAction SilentlyContinue
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remaining = Get-PortOwner $service.Port
        if ($remaining -eq 0) { break }
        if (-not (Test-WorkspaceProcess $remaining $ServiceName)) {
            throw "Port $($service.Port) is still occupied by PID $remaining."
        }
        Stop-ProcessTree $remaining
        Start-Sleep -Milliseconds 150
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
    $form.Size = New-Object System.Drawing.Size(680, 430)
    $form.MinimumSize = New-Object System.Drawing.Size(680, 430)
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
        $panel.Size = New-Object System.Drawing.Size(610, 82)
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

    Add-ServiceRow "backend" 92 $script:Strings.backend $script:Strings.backendHint
    Add-ServiceRow "frontend" 184 $script:Strings.frontend $script:Strings.frontendHint

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
    $openButton.Location = New-Object System.Drawing.Point(406, 286)
    $openButton.Size = New-Object System.Drawing.Size(112, 36)
    $openButton.FlatStyle = "Flat"
    $openButton.Add_Click({ Start-Process "http://127.0.0.1:5899/today" })
    $form.Controls.Add($openButton)

    $logsButton = New-Object System.Windows.Forms.Button
    $logsButton.Text = $script:Strings.openLogs
    $logsButton.Location = New-Object System.Drawing.Point(526, 286)
    $logsButton.Size = New-Object System.Drawing.Size(112, 36)
    $logsButton.FlatStyle = "Flat"
    $logsButton.Add_Click({ Initialize-LauncherState; Start-Process explorer.exe -ArgumentList ('"' + $script:LogRoot + '"') })
    $form.Controls.Add($logsButton)

    $footer = New-Object System.Windows.Forms.Label
    $footer.Text = $script:Strings.ready
    $footer.ForeColor = [System.Drawing.Color]::FromArgb(105, 115, 132)
    $footer.Location = New-Object System.Drawing.Point(31, 347)
    $footer.Size = New-Object System.Drawing.Size(600, 30)
    $form.Controls.Add($footer)

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1500
    $timer.Add_Tick({ Update-StatusLabels })
    $timer.Start()
    Update-StatusLabels
    [void]$form.ShowDialog()
    $timer.Stop()
}

if ($Action -eq "install-shortcut") { Install-DesktopShortcut; exit 0 }
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
Show-LauncherWindow
