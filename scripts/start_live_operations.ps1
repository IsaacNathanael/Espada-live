param(
    [int]$Port = 4180,
    [switch]$NoOpen,
    [switch]$Restart,
    [string]$PythonPath = "",
    [string]$RegionName = "Mumbai Offshore Watch",
    [double]$MinLongitude = 70.8,
    [double]$MinLatitude = 17.8,
    [double]$MaxLongitude = 73.0,
    [double]$MaxLatitude = 20.0
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
. (Join-Path $PSScriptRoot "_load_env.ps1")
Import-EspadaEnv -Path (Join-Path $ProjectRoot ".env")

if (-not $PythonPath) {
    foreach ($Candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) { $PythonPath = $Candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
$DefaultGpuPython = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
if ((-not $env:ESPADA_GPU_PYTHON) -and (Test-Path -LiteralPath $DefaultGpuPython)) {
    $env:ESPADA_GPU_PYTHON = $DefaultGpuPython
}
$Url = "http://127.0.0.1:$Port/operator/live_operations/index.html"
$HealthUrl = "http://127.0.0.1:$Port/api/live/health"
$Ready = $false
try {
    $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
    $Ready = $Health.status -eq "PASS"
}
catch { $Ready = $false }
if ($Restart) { $Ready = $false }

if (-not $Ready) {
    $PidDirectory = Join-Path $ProjectRoot "work"
    $PidFile = Join-Path $PidDirectory "live_operations_server.pid"
    if (Test-Path -LiteralPath $PidFile) {
        $OldPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($OldPid -match '^\d+$') {
            $OldProcess = Get-Process -Id ([int]$OldPid) -ErrorAction SilentlyContinue
            if ($OldProcess) { Stop-Process -Id $OldProcess.Id -Force; Start-Sleep -Milliseconds 250 }
        }
    }
    $Arguments = @(
        "-m", "espada.live_operations_server",
        "--root", ('"{0}"' -f $ProjectRoot),
        "--port", "$Port",
        "--name", ('"{0}"' -f ($RegionName -replace '"', '')),
        "--bbox", "$MinLongitude", "$MinLatitude", "$MaxLongitude", "$MaxLatitude"
    )
    New-Item -ItemType Directory -Force -Path $PidDirectory | Out-Null
    $StdOut = Join-Path $PidDirectory "live_operations_server.stdout.log"
    $StdErr = Join-Path $PidDirectory "live_operations_server.stderr.log"
    $Server = Start-Process -FilePath $PythonPath -ArgumentList $Arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $StdOut -RedirectStandardError $StdErr
    Set-Content -LiteralPath $PidFile -Value $Server.Id
    foreach ($Attempt in 1..50) {
        try {
            $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
            if ($Health.status -eq "PASS") { $Ready = $true; break }
        }
        catch { Start-Sleep -Milliseconds 200 }
    }
}
if (-not $Ready) {
    $StartupError = ""
    if (Test-Path -LiteralPath $StdErr) { $StartupError = Get-Content -LiteralPath $StdErr -Raw }
    throw "ESPADA Live Operations could not start on port $Port. $StartupError"
}

Write-Host "ESPADA LIVE OPERATIONS READY" -ForegroundColor Green
Write-Host $Url -ForegroundColor Yellow
Write-Host "Only provider-supplied objects are rendered. Empty feeds remain empty." -ForegroundColor Cyan
if (-not $env:AISSTREAM_API_KEY) {
    Write-Host "AISSTREAM_API_KEY is not loaded; the vessel layer will correctly show NOT CONFIGURED." -ForegroundColor Yellow
}
if (-not $NoOpen) {
    try { Start-Process -FilePath $Url }
    catch { Write-Host "Copy the address above into Chrome or Edge." -ForegroundColor Yellow }
}
