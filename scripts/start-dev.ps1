# Start OpenJarvis UI + API together (Windows).
# Double-click: scripts\Start-OpenJarvis.bat
# Or: powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

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

function Test-HttpOk([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$backendUrl = "http://127.0.0.1:8000/v1/info"
$frontendUrl = "http://127.0.0.1:5173/"
$backendPid = $null
$frontendPid = $null

$needBackend = -not (Test-HttpOk $backendUrl)
$needFrontend = -not ((Test-HttpOk $frontendUrl) -or (Test-HttpOk "http://localhost:5173/"))

if (-not $needBackend) {
    Write-Host "Backend already running on http://127.0.0.1:8000" -ForegroundColor Green
}
if (-not $needFrontend) {
    Write-Host "Frontend already running on http://127.0.0.1:5173" -ForegroundColor Green
}

# Start both at once (not one-after-another).
if ($needBackend) {
    Write-Host "Starting backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
    $backend = Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/c", "uv run jarvis serve --port 8000 --host 127.0.0.1") `
        -WorkingDirectory $Root -PassThru -WindowStyle Minimized
    $backendPid = $backend.Id
}

if ($needFrontend) {
    Write-Host "Starting frontend on http://127.0.0.1:5173 ..." -ForegroundColor Cyan
    $frontendDir = Join-Path $Root "frontend"
    $frontend = Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run dev -- --host 127.0.0.1 --port 5173") `
        -WorkingDirectory $frontendDir -PassThru -WindowStyle Minimized
    $frontendPid = $frontend.Id
}

if ($needBackend) {
    if (-not (Wait-HttpOk $backendUrl 90)) {
        Write-Host "ERROR: backend did not become ready. Check the minimized uv window." -ForegroundColor Red
        exit 1
    }
    Write-Host "Backend ready." -ForegroundColor Green
}

if ($needFrontend) {
    if (-not (Wait-HttpOk $frontendUrl 90)) {
        Write-Host "ERROR: frontend did not become ready. Run: cd frontend; npm install; npm run dev" -ForegroundColor Red
        exit 1
    }
    Write-Host "Frontend ready." -ForegroundColor Green
}

Start-Process $frontendUrl

Write-Host ""
Write-Host "OpenJarvis is ready:" -ForegroundColor Green
Write-Host "  UI:  http://127.0.0.1:5173/"
Write-Host "  API: http://127.0.0.1:8000/"
if ($backendPid) { Write-Host "  Backend PID:  $backendPid" }
if ($frontendPid) { Write-Host "  Frontend PID: $frontendPid" }
Write-Host ""
Write-Host "Next time: double-click scripts\Start-OpenJarvis.bat (or Desktop shortcut)."
