param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
try { (Get-Host).UI.RawUI.WindowTitle = "Kindler - stopping server" } catch { }

function Get-ListenerPid {
    param([int]$P)
    $conn = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    $line = netstat -ano -p TCP | Select-String ":$P\s.*LISTENING" | Select-Object -First 1
    if ($line) { return [int]($line.ToString().Trim() -split '\s+')[-1] }
    return $null
}

function Get-RootServerPid {
    param([int]$StartPid)
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$StartPid" -ErrorAction SilentlyContinue
    while ($current -and $current.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($current.ParentProcessId)" -ErrorAction SilentlyContinue
        if (-not $parent) { break }
        if ($parent.Name -notmatch '^(python|pythonw|uv|uvicorn)\.exe$') { break }
        $current = $parent
    }
    if ($current) { return [int]$current.ProcessId }
    return $StartPid
}

Clear-Host
Write-Host ""

$target = Get-ListenerPid -P $Port
if (-not $target) {
    Write-Host "  The Kindler server is not running." -ForegroundColor Gray
    Start-Sleep -Seconds 2
    exit 0
}

$rootPid = Get-RootServerPid -StartPid $target
Write-Host "  Stopping the Kindler server..." -ForegroundColor Yellow
& taskkill.exe /PID $rootPid /T /F 2>&1 | Out-Null

Start-Sleep -Milliseconds 700
$still = Get-ListenerPid -P $Port
if ($still) { & taskkill.exe /PID $still /T /F 2>&1 | Out-Null }

Start-Sleep -Milliseconds 400
if (Get-ListenerPid -P $Port) {
    Write-Host "  Could not stop the server - a reboot will clear it." -ForegroundColor Red
    Read-Host "  Press Enter to close"
} else {
    Write-Host "  Done - the server is stopped." -ForegroundColor Green
    Start-Sleep -Seconds 2
}
