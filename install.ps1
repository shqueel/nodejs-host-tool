<#
Sets up NPM Host on this machine:
  - Installs Python (with tkinter) if it isn't already present
  - Installs Node.js (npm) if it isn't already present
  - Creates a Desktop shortcut, and optionally a Startup-folder shortcut

Usage (from an elevated or non-elevated prompt - it will elevate itself):
    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -Startup   # also launch on login
#>

param(
    [switch]$Startup
)

$ErrorActionPreference = "Stop"

# Windows Server 2016 / older PowerShell defaults to TLS 1.0, which
# python.org and nodejs.org reject outright. Force TLS 1.2 up front.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Pinned installer versions. If either URL 404s because these have been
# pulled, grab the current version number from python.org/downloads or
# nodejs.org/en/download and update the two version strings below.
$PYTHON_VERSION = "3.13.1"
$PYTHON_URL = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe"
$NODE_VERSION = "22.11.0"
$NODE_URL = "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-x64.msi"

$ScriptDir = $PSScriptRoot
$AppScript = Join-Path $ScriptDir "npm_host.pyw"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $wp = New-Object Security.Principal.WindowsPrincipal($id)
    return $wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Write-Step($msg) {
    Write-Host ""
    Write-Host "== $msg ==" -ForegroundColor Cyan
}

function Test-Tkinter {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { return $false }
    & python -c "import tkinter" 2>$null
    return $LASTEXITCODE -eq 0
}

# ---- elevate if needed (installers require admin) ----
if (-not (Test-Admin)) {
    Write-Host "Administrator rights are required to install Python/Node.js. Relaunching elevated..." -ForegroundColor Yellow
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Startup) { $argList += "-Startup" }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

# ---- Python ----
Write-Step "Checking for Python"
if (Test-Tkinter) {
    Write-Host "Python with tkinter already installed: $(python --version)"
} else {
    Write-Host "Installing Python $PYTHON_VERSION (this can take a minute)..."
    $installer = Join-Path $env:TEMP "npmhost-python-installer.exe"
    try {
        Invoke-WebRequest -Uri $PYTHON_URL -OutFile $installer -UseBasicParsing
        Start-Process -FilePath $installer -ArgumentList @(
            "/quiet", "InstallAllUsers=1", "PrependPath=1",
            "Include_tcltk=1", "Include_launcher=1", "Include_test=0"
        ) -Wait
    } catch {
        Write-Host "Failed to download/install Python automatically: $_" -ForegroundColor Red
        Write-Host "Install it manually from https://www.python.org/downloads/ (make sure 'tcl/tk and IDLE' is checked) and re-run this script." -ForegroundColor Red
        exit 1
    } finally {
        Remove-Item $installer -ErrorAction SilentlyContinue
    }
    Update-SessionPath
    if (-not (Test-Tkinter)) {
        Write-Host "Python installed but tkinter still isn't available. Re-run the Python installer, choose Modify, and enable 'tcl/tk and IDLE'." -ForegroundColor Red
        exit 1
    }
    Write-Host "Python installed: $(python --version)" -ForegroundColor Green
}

# ---- Node.js / npm ----
Write-Step "Checking for Node.js / npm"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "npm already installed: $(npm --version)"
} else {
    Write-Host "Installing Node.js $NODE_VERSION (this can take a minute)..."
    $installer = Join-Path $env:TEMP "npmhost-node-installer.msi"
    try {
        Invoke-WebRequest -Uri $NODE_URL -OutFile $installer -UseBasicParsing
        Start-Process msiexec.exe -ArgumentList @("/i", "`"$installer`"", "/quiet", "/norestart") -Wait
    } catch {
        Write-Host "Failed to download/install Node.js automatically: $_" -ForegroundColor Red
        Write-Host "Install it manually from https://nodejs.org/ and re-run this script." -ForegroundColor Red
        exit 1
    } finally {
        Remove-Item $installer -ErrorAction SilentlyContinue
    }
    Update-SessionPath
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "Node.js installed but npm isn't on PATH in this session yet. Log out and back in, then re-run this script to add the shortcuts." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "npm installed: $(npm --version)" -ForegroundColor Green
}

# ---- Shortcuts ----
Write-Step "Creating shortcuts"
Update-SessionPath
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    Write-Host "Could not locate pythonw.exe on PATH. Skipping shortcut creation - re-run this script after confirming Python installed correctly." -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell

function New-AppShortcut($path) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $pythonw
    $s.Arguments = "`"$AppScript`""
    $s.WorkingDirectory = $ScriptDir
    $s.IconLocation = $pythonw
    $s.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
New-AppShortcut (Join-Path $desktop "NPM Host.lnk")
Write-Host "Desktop shortcut created: $desktop\NPM Host.lnk" -ForegroundColor Green

if ($Startup) {
    $startupDir = [Environment]::GetFolderPath("Startup")
    New-AppShortcut (Join-Path $startupDir "NPM Host.lnk")
    Write-Host "Startup shortcut created: $startupDir\NPM Host.lnk (launches automatically on login)" -ForegroundColor Green
}

Write-Step "Done"
Write-Host "Launch NPM Host from the Desktop shortcut." -ForegroundColor Green
