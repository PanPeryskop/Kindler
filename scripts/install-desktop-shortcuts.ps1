param(
    [string]$Name = 'Kindler',
    [string]$Parent = [Environment]::GetFolderPath('Desktop'),
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Scripts = $PSScriptRoot
$Root    = Split-Path -Parent $Scripts
$Icon    = Join-Path $Scripts 'kindler.ico'
$PSExe   = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Folder  = Join-Path $Parent $Name

if (-not (Test-Path $Folder)) { New-Item -ItemType Directory -Path $Folder | Out-Null }

$shell = New-Object -ComObject WScript.Shell

function New-AppShortcut {
    param([string]$FileName, [string]$Script, [string]$Description)
    $path = Join-Path $Folder $FileName
    $link = $shell.CreateShortcut($path)
    $link.TargetPath       = $PSExe
    $link.Arguments        = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Port $Port"
    $link.WorkingDirectory = $Root
    $link.IconLocation     = "$Icon,0"
    $link.Description      = $Description
    $link.WindowStyle      = 1
    $link.Save()
    Write-Host "  Created: $path" -ForegroundColor Green
}

New-AppShortcut -FileName 'Turn On.lnk' `
    -Script (Join-Path $Scripts 'start-kindler.ps1') `
    -Description 'Start the local Send-to-Kindle server and open it in the browser'

New-AppShortcut -FileName 'Turn Off.lnk' `
    -Script (Join-Path $Scripts 'stop-kindler.ps1') `
    -Description 'Force-stop the local Send-to-Kindle server'

$iniPath = Join-Path $Folder 'desktop.ini'
if (Test-Path $iniPath) { attrib.exe -h -s -r "$iniPath" | Out-Null }
$ini = @"
[.ShellClassInfo]
ConfirmFileOp=0
IconResource=$Icon,0
IconFile=$Icon
IconIndex=0
"@
[IO.File]::WriteAllText($iniPath, $ini, [Text.Encoding]::Unicode)
attrib.exe +h +s "$iniPath" | Out-Null
attrib.exe +r "$Folder" | Out-Null

$stale = Get-ChildItem -Path $Parent -Filter '*.lnk' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "$Name.lnk" -or $_.Name -like "$Name - *.lnk" }
foreach ($item in $stale) { Remove-Item $item.FullName -Force -ErrorAction SilentlyContinue }

& ie4uinit.exe -show 2>&1 | Out-Null

Write-Host ""
Write-Host "  Done. Folder with shortcuts: $Folder" -ForegroundColor Cyan
