# Create a Desktop shortcut that starts UI + API together.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot "Start-OpenJarvis.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "OpenJarvis.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $bat
$lnk.WorkingDirectory = $Root
$lnk.WindowStyle = 1
$lnk.Description = "Start OpenJarvis (frontend + backend)"
$lnk.Save()

Write-Host "Shortcut created: $lnkPath" -ForegroundColor Green
Write-Host "Double-click it to start UI + API together."
