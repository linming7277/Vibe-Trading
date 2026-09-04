# Modern WinForms desktop UI for the Hengzhi local workbench launcher.
# Dot-sourced by HengzhiLauncher.ps1; expects $script:Strings, service helpers, and URLs.

function New-HzColor([int]$R, [int]$G, [int]$B) {
    return [System.Drawing.Color]::FromArgb($R, $G, $B)
}

function New-HzFont([string]$Family, [float]$Size, [System.Drawing.FontStyle]$Style = "Regular") {
    return New-Object System.Drawing.Font($Family, $Size, $Style, [System.Drawing.GraphicsUnit]::Point)
}

function Set-HzButtonStyle(
    [System.Windows.Forms.Button]$Button,
    [string]$Variant = "secondary"
) {
    $Button.FlatStyle = "Flat"
    $Button.FlatAppearance.BorderSize = 1
    $Button.Cursor = [System.Windows.Forms.Cursors]::Hand
    $Button.Font = New-HzFont "Microsoft YaHei UI" 9
    switch ($Variant) {
        "primary" {
            $Button.BackColor = New-HzColor 37 99 235
            $Button.ForeColor = [System.Drawing.Color]::White
            $Button.FlatAppearance.BorderColor = New-HzColor 37 99 235
        }
        "accent" {
            $Button.BackColor = New-HzColor 234 88 12
            $Button.ForeColor = [System.Drawing.Color]::White
            $Button.FlatAppearance.BorderColor = New-HzColor 234 88 12
        }
        "danger" {
            $Button.BackColor = [System.Drawing.Color]::White
            $Button.ForeColor = New-HzColor 220 38 38
            $Button.FlatAppearance.BorderColor = New-HzColor 252 165 165
        }
        default {
            $Button.BackColor = [System.Drawing.Color]::White
            $Button.ForeColor = New-HzColor 30 41 59
            $Button.FlatAppearance.BorderColor = New-HzColor 226 232 240
        }
    }
}

function New-HzCard([System.Windows.Forms.Control]$Parent, [int]$Left, [int]$Top, [int]$Width, [int]$Height) {
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Location = New-Object System.Drawing.Point($Left, $Top)
    $panel.Size = New-Object System.Drawing.Size($Width, $Height)
    $panel.BackColor = [System.Drawing.Color]::White
    $panel.Padding = New-Object System.Windows.Forms.Padding 18, 16, 18, 16
    $panel.Add_Paint({
        param($sender, $e)
        $rect = $sender.ClientRectangle
        $rect.Width -= 1
        $rect.Height -= 1
        $e.Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $pen = New-Object System.Drawing.Pen (New-HzColor 226 232 240)
        $e.Graphics.DrawRectangle($pen, $rect)
        $pen.Dispose()
    })
    $Parent.Controls.Add($panel)
    return $panel
}

function Get-HzStatusPresentation([string]$Status) {
    switch ($Status) {
        "running" { return @{ Text = $script:Strings.running; Color = New-HzColor 22 163 74 } }
        "workspace" { return @{ Text = $script:Strings.managedElsewhere; Color = New-HzColor 37 99 235 } }
        "starting" { return @{ Text = $script:Strings.starting; Color = New-HzColor 217 119 6 } }
        "conflict" { return @{ Text = $script:Strings.conflict; Color = New-HzColor 220 38 38 } }
        default { return @{ Text = $script:Strings.stopped; Color = New-HzColor 100 116 139 } }
    }
}

function Copy-TextToClipboard([string]$Text, [System.Windows.Forms.Label]$FeedbackLabel) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return }
    try {
        [System.Windows.Forms.Clipboard]::SetText($Text)
        if ($FeedbackLabel) {
            $FeedbackLabel.Text = $script:Strings.copied
            $timer = New-Object System.Windows.Forms.Timer
            $timer.Interval = 1800
            $timer.Add_Tick({
                $FeedbackLabel.Text = ""
                $timer.Stop()
                $timer.Dispose()
            })
            $timer.Start()
        }
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null
    }
}

function Show-LauncherWindow {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    Initialize-LauncherState

    $iconPath = Join-Path $script:RepoRoot "assets\hz-icon.ico"
    if (-not (Test-Path -LiteralPath $iconPath)) {
        $iconScript = Join-Path $PSScriptRoot "New-HzIcon.ps1"
        if (Test-Path -LiteralPath $iconScript) {
            & $iconScript | Out-Null
        }
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = $script:Strings.title
    $form.Size = New-Object System.Drawing.Size(900, 700)
    $form.MinimumSize = New-Object System.Drawing.Size(900, 700)
    $form.StartPosition = "CenterScreen"
    $form.Font = New-HzFont "Microsoft YaHei UI" 9
    $form.BackColor = New-HzColor 248 250 252
    $form.FormBorderStyle = "FixedSingle"
    $form.MaximizeBox = $false
    if (Test-Path -LiteralPath $iconPath) {
        $form.Icon = New-Object System.Drawing.Icon $iconPath
    }

    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = "Top"
    $header.Height = 96
    $header.Add_Paint({
        param($sender, $e)
        $rect = $sender.ClientRectangle
        $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            $rect,
            [System.Drawing.Color]::FromArgb(255, 234, 88, 12),
            [System.Drawing.Color]::FromArgb(255, 247, 163, 22),
            0
        )
        $e.Graphics.FillRectangle($brush, $rect)
        $brush.Dispose()
    })
    $form.Controls.Add($header)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = $script:Strings.title
    $title.Font = New-HzFont "Microsoft YaHei UI" 20 "Bold"
    $title.ForeColor = [System.Drawing.Color]::White
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(28, 22)
    $title.BackColor = [System.Drawing.Color]::Transparent
    $header.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = $script:Strings.subtitle
    $subtitle.Font = New-HzFont "Microsoft YaHei UI" 9.5
    $subtitle.ForeColor = [System.Drawing.Color]::FromArgb(255, 255, 255, 230)
    $subtitle.AutoSize = $true
    $subtitle.Location = New-Object System.Drawing.Point(30, 58)
    $subtitle.BackColor = [System.Drawing.Color]::Transparent
    $header.Controls.Add($subtitle)

    $statusSummary = New-Object System.Windows.Forms.Label
    $statusSummary.Font = New-HzFont "Microsoft YaHei UI" 9
    $statusSummary.ForeColor = [System.Drawing.Color]::FromArgb(255, 255, 255, 235)
    $statusSummary.AutoSize = $true
    $statusSummary.Location = New-Object System.Drawing.Point(620, 36)
    $statusSummary.BackColor = [System.Drawing.Color]::Transparent
    $header.Controls.Add($statusSummary)

    $content = New-Object System.Windows.Forms.Panel
    $content.Dock = "Fill"
    $content.Padding = New-Object System.Windows.Forms.Padding 24, 20, 24, 16
    $content.BackColor = New-HzColor 248 250 252
    $form.Controls.Add($content)
    $content.BringToFront()

    $statusLabels = @{}
    $serviceMeta = @{
        backend = @{ Display = $script:Strings.backend; Hint = $script:Strings.backendHint; Port = "8899" }
        frontend = @{ Display = $script:Strings.frontend; Hint = $script:FrontendUrl; Port = "5899" }
    }

    function Add-ServiceCard([string]$serviceName, [int]$left) {
        $meta = $serviceMeta[$serviceName]
        $card = New-HzCard $content $left 0 404 148

        $nameLabel = New-Object System.Windows.Forms.Label
        $nameLabel.Text = $meta.Display
        $nameLabel.Font = New-HzFont "Microsoft YaHei UI" 12 "Bold"
        $nameLabel.ForeColor = New-HzColor 30 41 59
        $nameLabel.Location = New-Object System.Drawing.Point(18, 16)
        $nameLabel.AutoSize = $true
        $card.Controls.Add($nameLabel)

        $portLabel = New-Object System.Windows.Forms.Label
        $portLabel.Text = "$($script:Strings.portLabel) $($meta.Port)"
        $portLabel.ForeColor = New-HzColor 100 116 139
        $portLabel.Location = New-Object System.Drawing.Point(19, 42)
        $portLabel.AutoSize = $true
        $card.Controls.Add($portLabel)

        $hintLabel = New-Object System.Windows.Forms.Label
        $hintLabel.Text = $meta.Hint
        $hintLabel.ForeColor = New-HzColor 100 116 139
        $hintLabel.Location = New-Object System.Drawing.Point(19, 64)
        $hintLabel.Size = New-Object System.Drawing.Size(360, 18)
        $hintLabel.AutoEllipsis = $true
        $card.Controls.Add($hintLabel)

        $stateLabel = New-Object System.Windows.Forms.Label
        $stateLabel.Location = New-Object System.Drawing.Point(18, 92)
        $stateLabel.Size = New-Object System.Drawing.Size(360, 24)
        $stateLabel.Font = New-HzFont "Microsoft YaHei UI" 10 "Bold"
        $card.Controls.Add($stateLabel)
        $statusLabels[$serviceName] = $stateLabel

        $buttonLeft = 18
        foreach ($definition in @(
            @($script:Strings.start, "start", "secondary"),
            @($script:Strings.restart, "restart", "secondary"),
            @($script:Strings.stop, "stop", "danger")
        )) {
            $button = New-Object System.Windows.Forms.Button
            $button.Text = $definition[0]
            $button.Tag = "$serviceName|$($definition[1])"
            $button.Location = New-Object System.Drawing.Point($buttonLeft, 112)
            $button.Size = New-Object System.Drawing.Size(72, 30)
            Set-HzButtonStyle $button $definition[2]
            $button.Add_Click({
                $parts = [string]$this.Tag -split "\|"
                try {
                    if ($parts[1] -eq "start") { Start-LauncherService $parts[0] | Out-Null }
                    elseif ($parts[1] -eq "restart") { Restart-LauncherService $parts[0] | Out-Null }
                    else { Stop-LauncherService $parts[0] }
                    Update-StatusLabels
                }
                catch {
                    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null
                }
            })
            $card.Controls.Add($button)
            $buttonLeft += 78
        }
    }

    Add-ServiceCard "backend" 0
    Add-ServiceCard "frontend" 420

    $toolbar = New-Object System.Windows.Forms.Panel
    $toolbar.Location = New-Object System.Drawing.Point(0, 160)
    $toolbar.Size = New-Object System.Drawing.Size(824, 44)
    $toolbar.BackColor = [System.Drawing.Color]::Transparent
    $content.Controls.Add($toolbar)

    $copyFeedback = New-Object System.Windows.Forms.Label
    $copyFeedback.ForeColor = New-HzColor 22 163 74
    $copyFeedback.Location = New-Object System.Drawing.Point(700, 12)
    $copyFeedback.Size = New-Object System.Drawing.Size(120, 20)
    $copyFeedback.TextAlign = "MiddleRight"
    $toolbar.Controls.Add($copyFeedback)

    $toolbarButtons = @(
        @($script:Strings.startAll, "start", "accent", 0),
        @($script:Strings.restartAll, "restart", "secondary", 108),
        @($script:Strings.stopAll, "stop", "danger", 216),
        @($script:Strings.openWorkbench, "open", "primary", 340),
        @($script:Strings.copyLanUrl, "copy-lan", "secondary", 468),
        @($script:Strings.openLogs, "logs", "secondary", 596)
    )
    foreach ($definition in $toolbarButtons) {
        $button = New-Object System.Windows.Forms.Button
        $button.Text = $definition[0]
        $button.Tag = $definition[1]
        $button.Location = New-Object System.Drawing.Point([int]$definition[3], 4)
        $button.Size = New-Object System.Drawing.Size(100, 34)
        Set-HzButtonStyle $button $definition[2]
        $button.Add_Click({
            $action = [string]$this.Tag
            try {
                switch ($action) {
                    "start" { Invoke-All "start" }
                    "restart" { Invoke-All "restart" }
                    "stop" { Invoke-All "stop" }
                    "open" { Start-Process $script:FrontendUrl }
                    "copy-lan" {
                        $url = if ($script:LanAddress) { "http://$($script:LanAddress):5899/value" } else { $script:FrontendUrl }
                        Copy-TextToClipboard $url $copyFeedback
                    }
                    "logs" {
                        Initialize-LauncherState
                        Start-Process explorer.exe -ArgumentList ('"' + $script:LogRoot + '"')
                    }
                }
                if ($action -in @("start", "restart", "stop")) { Update-StatusLabels }
            }
            catch {
                [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null
            }
        })
        $toolbar.Controls.Add($button)
    }

    $urlCard = New-HzCard $content 0 214 824 88
    $urlTitle = New-Object System.Windows.Forms.Label
    $urlTitle.Text = $script:Strings.accessTitle
    $urlTitle.Font = New-HzFont "Microsoft YaHei UI" 10 "Bold"
    $urlTitle.ForeColor = New-HzColor 30 41 59
    $urlTitle.Location = New-Object System.Drawing.Point(18, 14)
    $urlTitle.AutoSize = $true
    $urlCard.Controls.Add($urlTitle)

    $localUrlLabel = New-Object System.Windows.Forms.Label
    $localUrlLabel.Text = "$($script:Strings.localUrlPrefix)：$($script:FrontendUrl)"
    $localUrlLabel.ForeColor = New-HzColor 51 65 85
    $localUrlLabel.Location = New-Object System.Drawing.Point(19, 40)
    $localUrlLabel.Size = New-Object System.Drawing.Size(620, 18)
    $localUrlLabel.AutoEllipsis = $true
    $urlCard.Controls.Add($localUrlLabel)

    $lanUrlLabel = New-Object System.Windows.Forms.Label
    $lanUrlLabel.Text = "$($script:Strings.lanUrlPrefix)：$($script:LanFrontendUrl)"
    $lanUrlLabel.ForeColor = New-HzColor 51 65 85
    $lanUrlLabel.Location = New-Object System.Drawing.Point(19, 62)
    $lanUrlLabel.Size = New-Object System.Drawing.Size(620, 18)
    $lanUrlLabel.AutoEllipsis = $true
    $urlCard.Controls.Add($lanUrlLabel)

    $copyLocalButton = New-Object System.Windows.Forms.Button
    $copyLocalButton.Text = $script:Strings.copy
    $copyLocalButton.Location = New-Object System.Drawing.Point(650, 36)
    $copyLocalButton.Size = New-Object System.Drawing.Size(72, 30)
    Set-HzButtonStyle $copyLocalButton "secondary"
    $copyLocalButton.Add_Click({ Copy-TextToClipboard $script:FrontendUrl $copyFeedback })
    $urlCard.Controls.Add($copyLocalButton)

    $copyLanButton = New-Object System.Windows.Forms.Button
    $copyLanButton.Text = $script:Strings.copy
    $copyLanButton.Location = New-Object System.Drawing.Point(728, 36)
    $copyLanButton.Size = New-Object System.Drawing.Size(72, 30)
    Set-HzButtonStyle $copyLanButton "secondary"
    $copyLanButton.Add_Click({
        $url = if ($script:LanAddress) { "http://$($script:LanAddress):5899/value" } else { $script:FrontendUrl }
        Copy-TextToClipboard $url $copyFeedback
    })
    $urlCard.Controls.Add($copyLanButton)

    $accessTitle = New-Object System.Windows.Forms.Label
    $accessTitle.Text = $script:Strings.recentClients
    $accessTitle.Font = New-HzFont "Microsoft YaHei UI" 10 "Bold"
    $accessTitle.ForeColor = New-HzColor 30 41 59
    $accessTitle.Location = New-Object System.Drawing.Point(2, 316)
    $accessTitle.AutoSize = $true
    $content.Controls.Add($accessTitle)

    $clientList = New-Object System.Windows.Forms.ListView
    $clientList.Location = New-Object System.Drawing.Point(0, 342)
    $clientList.Size = New-Object System.Drawing.Size(824, 170)
    $clientList.View = [System.Windows.Forms.View]::Details
    $clientList.FullRowSelect = $true
    $clientList.GridLines = $false
    $clientList.BorderStyle = "FixedSingle"
    $clientList.HeaderStyle = "Nonclickable"
    $clientList.MultiSelect = $false
    $clientList.Font = New-HzFont "Microsoft YaHei UI" 9
    [void]$clientList.Columns.Add($script:Strings.colDeviceIp, 240)
    [void]$clientList.Columns.Add($script:Strings.colFirstSeen, 180)
    [void]$clientList.Columns.Add($script:Strings.colLastSeen, 180)
    [void]$clientList.Columns.Add($script:Strings.colRequestCount, 100)
    $content.Controls.Add($clientList)

    $accessHint = New-Object System.Windows.Forms.Label
    $accessHint.ForeColor = New-HzColor 100 116 139
    $accessHint.Location = New-Object System.Drawing.Point(2, 518)
    $accessHint.Size = New-Object System.Drawing.Size(820, 20)
    $content.Controls.Add($accessHint)

    $footer = New-Object System.Windows.Forms.Label
    $footer.Text = $script:Strings.ready
    $footer.ForeColor = New-HzColor 100 116 139
    $footer.Location = New-Object System.Drawing.Point(2, 542)
    $footer.Size = New-Object System.Drawing.Size(820, 36)
    $content.Controls.Add($footer)

    function Update-StatusLabels {
        $runningCount = 0
        foreach ($serviceName in @("backend", "frontend")) {
            $state = Get-ServiceState $serviceName
            $label = $statusLabels[$serviceName]
            $presentation = Get-HzStatusPresentation $state.Status
            $label.Text = $presentation.Text
            $label.ForeColor = $presentation.Color
            if ($state.Status -in @("running", "workspace", "starting")) { $runningCount++ }
        }
        $statusSummary.Text = if ($runningCount -eq 2) { $script:Strings.allRunning } elseif ($runningCount -eq 0) { $script:Strings.allStopped } else { $script:Strings.partialRunning }
    }

    $uiState = @{ ClientSignature = "" }
    function Update-ClientList {
        $clients = @(Get-RecentClientAccess)
        $signature = ($clients | ForEach-Object { "$($_.ip)|$($_.first_seen)|$($_.last_seen)|$($_.request_count)" }) -join "`n"
        if ($signature -eq $uiState.ClientSignature) { return }
        $uiState.ClientSignature = $signature
        $clientList.BeginUpdate()
        try {
            $clientList.Items.Clear()
            foreach ($client in $clients) {
                $ip = [string]$client.ip
                $displayIp = if ($ip -eq "127.0.0.1" -or $ip -eq "::1") { "$ip$($script:Strings.localClientSuffix)" } else { $ip }
                $item = New-Object System.Windows.Forms.ListViewItem($displayIp)
                [void]$item.SubItems.Add((Format-ClientAccessTime $client.first_seen))
                [void]$item.SubItems.Add((Format-ClientAccessTime $client.last_seen))
                [void]$item.SubItems.Add([string]$client.request_count)
                [void]$clientList.Items.Add($item)
            }
        }
        finally { $clientList.EndUpdate() }
        $accessHint.Text = if ($clients.Count) { $script:Strings.clientHintActive } else { $script:Strings.clientHintEmpty }
    }

    $notifyIcon = New-Object System.Windows.Forms.NotifyIcon
    if (Test-Path -LiteralPath $iconPath) {
        $notifyIcon.Icon = New-Object System.Drawing.Icon $iconPath
    }
    $notifyIcon.Text = $script:Strings.title
    $notifyIcon.Visible = $true

    $trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
    $showItem = $trayMenu.Items.Add($script:Strings.showWindow)
    $openItem = $trayMenu.Items.Add($script:Strings.openWorkbench)
    $exitItem = $trayMenu.Items.Add($script:Strings.exitLauncher)
    $notifyIcon.ContextMenuStrip = $trayMenu

    $showItem.Add_Click({ $form.Show(); $form.WindowState = "Normal"; $form.Activate() })
    $openItem.Add_Click({ Start-Process $script:FrontendUrl })
    $exitItem.Add_Click({
        $notifyIcon.Visible = $false
        $notifyIcon.Dispose()
        $form.Close()
    })
    $notifyIcon.Add_DoubleClick({ $form.Show(); $form.WindowState = "Normal"; $form.Activate() })

    $form.Add_Resize({
        if ($form.WindowState -eq "Minimized") {
            $form.Hide()
            $notifyIcon.BalloonTipTitle = $script:Strings.title
            $notifyIcon.BalloonTipText = $script:Strings.minimizedToTray
            $notifyIcon.ShowBalloonTip(1200)
        }
    })
    $form.Add_FormClosing({
        if ($_.CloseReason -eq "UserClosing") {
            $_.Cancel = $true
            $form.Hide()
            $notifyIcon.BalloonTipTitle = $script:Strings.title
            $notifyIcon.BalloonTipText = $script:Strings.minimizedToTray
            $notifyIcon.ShowBalloonTip(1200)
        }
    })

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1500
    $timer.Add_Tick({ Update-StatusLabels; Update-ClientList })
    $timer.Start()
    Update-StatusLabels
    Update-ClientList

    if ($AutoStart) {
        $form.Add_Shown({
            try {
                $backend = Get-ServiceState "backend"
                $frontend = Get-ServiceState "frontend"
                if ($backend.Status -notin @("running", "workspace") -or $frontend.Status -notin @("running", "workspace")) {
                    Invoke-All "start"
                    Update-StatusLabels
                }
                Start-Process $script:FrontendUrl
            }
            catch {
                [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, $script:Strings.errorTitle, "OK", "Error") | Out-Null
            }
        })
    }

    [void]$form.ShowDialog()
    $timer.Stop()
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
}
