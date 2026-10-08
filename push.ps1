param(
    [int]$Count = 6,
    [string[]]$Exclude = @("短剧"),
    [string[]]$Include = @(),
    [string]$Kw = "",
    [ValidateSet("quark", "baidu", "all")]
    [string]$Platform = "quark",
    [switch]$Force,
    [switch]$Preview,
    [switch]$SkipPanSouCheck
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-PanSou {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8888/api/health" `
            -UseBasicParsing -TimeoutSec 3
        $json = $resp.Content | ConvertFrom-Json
        return $json.status -eq "ok"
    } catch {
        return $false
    }
}

function Start-PanSou {
    Write-Host "[1/2] PanSou not running, starting..." -ForegroundColor Yellow
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = "-ExecutionPolicy Bypass -File `"$PSScriptRoot\start_pansou.ps1`""
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Minimized
    [void][System.Diagnostics.Process]::Start($psi)

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if (Test-PanSou) {
            Write-Host "      PanSou ready (http://localhost:8888)" -ForegroundColor Green
            return $true
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "      PanSou startup timeout (60s), please check manually" -ForegroundColor Red
    return $false
}

if (-not $SkipPanSouCheck) {
    if (Test-PanSou) {
        Write-Host "[1/2] PanSou already running (http://localhost:8888)" -ForegroundColor Green
    } else {
        if (-not (Start-PanSou)) {
            exit 1
        }
    }
}

$cmdLine = @($Platform, "--count", "$Count")
foreach ($e in $Exclude) { $cmdLine += @("--exclude", $e) }
foreach ($i in $Include) { $cmdLine += @("--include", $i) }
if ($Kw)       { $cmdLine += @("--kw", $Kw) }
if ($Force)    { $cmdLine += "--force" }
if ($Preview)  { $cmdLine += "--preview" }

Write-Host "[2/2] Running: python main.py $($cmdLine -join ' ')" -ForegroundColor Cyan
Write-Host ""

& python main.py @cmdLine
exit $LASTEXITCODE
