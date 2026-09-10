param(
    [int]$Port = 8000,
    [string]$AppModule = 'app.main:app',
    [switch]$NoReload,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $PSScriptRoot
$Url  = "http://127.0.0.1:$Port"

try { (Get-Host).UI.RawUI.WindowTitle = "Kindler - server running on port $Port" } catch { }

function Get-ListenerPid {
    param([int]$P)
    try {
        $conn = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn) { return [int]$conn.OwningProcess }
    } catch {
        $line = netstat -ano -p TCP | Select-String ":$P\s.*LISTENING" | Select-Object -First 1
        if ($line) { return [int]($line.ToString().Trim() -split '\s+')[-1] }
    }
    return $null
}

function Stop-Server {
    param($Proc)
    Write-Host ""
    Write-Host "  Stopping server..." -ForegroundColor Yellow
    if ($Proc -and -not $Proc.HasExited) {
        & taskkill.exe /PID $Proc.Id /T /F 2>&1 | Out-Null
    }
    $leftover = Get-ListenerPid -P $Port
    if ($leftover) { & taskkill.exe /PID $leftover /T /F 2>&1 | Out-Null }
    Write-Host "  Server stopped." -ForegroundColor Green
    Start-Sleep -Milliseconds 900
}

Clear-Host
Write-Host ""
Write-Host "   ==============================" -ForegroundColor DarkCyan
Write-Host "         K I N D L E R" -ForegroundColor Cyan
Write-Host "   ==============================" -ForegroundColor DarkCyan
Write-Host ""

if (Get-ListenerPid -P $Port) {
    Write-Host "  Server is already running - opening the browser." -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $Url }
    Start-Sleep -Seconds 2
    exit 0
}

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    foreach ($candidate in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:LOCALAPPDATA\Programs\uv\uv.exe")) {
        if (Test-Path $candidate) { $uv = $candidate; break }
    }
}
if (-not $uv) {
    Write-Host "  ERROR: 'uv' was not found." -ForegroundColor Red
    Write-Host "  Install uv (https://docs.astral.sh/uv/) and try again." -ForegroundColor Red
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

if (-not (Test-Path (Join-Path $Root '.env'))) {
    Write-Host "  Warning: no .env file found - sending e-mail may not work." -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "  Starting the server... (the first run may take a minute)" -ForegroundColor Gray
Write-Host ""

$uvArgs = @('run', 'uvicorn', $AppModule, '--port', "$Port")
if (-not $NoReload) { $uvArgs += '--reload' }

$proc = Start-Process -FilePath $uv -ArgumentList $uvArgs `
    -WorkingDirectory $Root -NoNewWindow -PassThru

try {
    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { break }
        if (Get-ListenerPid -P $Port) { $ready = $true; break }
        Start-Sleep -Milliseconds 400
    }

    if (-not $ready) {
        Write-Host ""
        Write-Host "  The server failed to start - see the messages above." -ForegroundColor Red
        Write-Host ""
        Read-Host "  Press Enter to close"
        exit 1
    }

    if (-not $NoBrowser) { Start-Process $Url }

    Write-Host ""
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "   Server is running:  $Url" -ForegroundColor Green
    Write-Host ""
    Write-Host "   TO STOP: press Q, or simply close this window." -ForegroundColor Yellow
    Write-Host "   Keep this window open while you use the site." -ForegroundColor Gray
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host ""

    while (-not $proc.HasExited) {
        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq 'Q' -or $key.Key -eq 'Escape') { break }
            }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
}
finally {
    Stop-Server -Proc $proc
}
