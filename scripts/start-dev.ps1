# Start OpenJarvis for local development (Windows)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Refresh PATH (Rust / uv / cargo)
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

# Load cloud keys from ~/.openjarvis/cloud-keys.env and User env
$keysFile = Join-Path $env:USERPROFILE ".openjarvis\cloud-keys.env"
if (Test-Path $keysFile) {
    Get-Content $keysFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $k, $v = $line.Split("=", 2)
            Set-Item -Path "Env:$($k.Trim())" -Value $v.Trim()
        }
    }
}
$userKey = [System.Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
if ($userKey) { $env:DEEPSEEK_API_KEY = $userKey }

if (-not $env:DEEPSEEK_API_KEY) {
    Write-Host "WARNING: DEEPSEEK_API_KEY is not set." -ForegroundColor Yellow
}

Write-Host "Starting backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$backend = Start-Process -FilePath "uv" -ArgumentList @("run", "jarvis", "serve", "--port", "8000", "--host", "127.0.0.1") `
    -WorkingDirectory $Root -PassThru -WindowStyle Minimized

Start-Sleep -Seconds 3

Write-Host "Starting frontend on http://localhost:5173 ..." -ForegroundColor Cyan
$frontend = Start-Process -FilePath "npm" -ArgumentList @("run", "dev") `
    -WorkingDirectory (Join-Path $Root "frontend") -PassThru -WindowStyle Minimized

Start-Sleep -Seconds 4
Start-Process "http://localhost:5173/"

Write-Host ""
Write-Host "OpenJarvis is starting:" -ForegroundColor Green
Write-Host "  UI:      http://localhost:5173/"
Write-Host "  API:     http://127.0.0.1:8000/"
Write-Host "  Backend PID:  $($backend.Id)"
Write-Host "  Frontend PID: $($frontend.Id)"
Write-Host ""
Write-Host "Ctrl+K -> Cloud Models -> DeepSeek -> deepseek-v4-flash"
Write-Host "Settings -> Speech -> enable mic if needed"
