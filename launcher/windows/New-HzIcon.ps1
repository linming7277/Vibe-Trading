# Generates assets/hz-icon.ico from brand colors (run once or on install).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$iconPath = Join-Path $repoRoot "assets\hz-icon.ico"
New-Item -ItemType Directory -Path (Split-Path $iconPath) -Force | Out-Null

$size = 64
$bmp = New-Object System.Drawing.Bitmap $size, $size
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)

$rect = New-Object System.Drawing.Rectangle 0, 0, $size, $size
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $rect,
    [System.Drawing.Color]::FromArgb(255, 234, 88, 12),
    [System.Drawing.Color]::FromArgb(255, 247, 163, 22),
    45
)
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddArc(4, 4, 56, 56, 0, 360)
$g.FillPath($brush, $path)

$white = [System.Drawing.Brushes]::White
$g.FillRectangle($white, 14, 30, 6, 12)
$g.FillRectangle($white, 29, 22, 6, 14)
$g.FillRectangle($white, 44, 16, 6, 16)

$icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
$stream = [System.IO.File]::Create($iconPath)
$icon.Save($stream)
$stream.Close()
$g.Dispose()
$bmp.Dispose()

Write-Host "Icon saved: $iconPath"
