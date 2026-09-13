param(
    [string]$PythonPath = "",
    [int]$Port = 4173,
    [switch]$NoOpen,
    [switch]$UseExistingDashboard
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$DashboardPath = Join-Path $ProjectRoot "out\demo\dashboard.html"

if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $PythonPath = $candidate
            break
        }
    }
}

if (-not $PythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $PythonPath = $pythonCommand.Source
    }
}

if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python was not found. Pass -PythonPath with any available Python 3 installation."
}

$required = @(
    (Join-Path $ProjectRoot "out\demo\candidates.json"),
    (Join-Path $ProjectRoot "out\demo\attribution_map.png"),
    (Join-Path $ProjectRoot "out\demo\physics\verification_result.json"),
    (Join-Path $ProjectRoot "out\demo\environment\environment_status.json")
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Showcase evidence is missing: $path. Run scripts\run_demo.ps1 once in a working environment."
    }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$demoDirectory = Join-Path $ProjectRoot "out\demo"
$escapedDemoDirectory = $demoDirectory.Replace("'", "''")

if ($UseExistingDashboard) {
    if (-not (Test-Path -LiteralPath $DashboardPath)) {
        throw "The existing showcase page is missing: $DashboardPath"
    }
}
else {
    & $PythonPath -c "from pathlib import Path; from espada.dashboard import generate_dashboard; print(generate_dashboard(Path(r'$escapedDemoDirectory')))"
    if ($LASTEXITCODE -ne 0) {
        throw "The showcase could not be generated."
    }
}

Write-Host ""
Write-Host "ESPADA SHOWCASE READY" -ForegroundColor Green
Write-Host "The page is self-contained and works offline." -ForegroundColor Cyan

$ShowcaseUrl = "http://127.0.0.1:$Port/out/demo/dashboard.html"
$HealthUrl = "http://127.0.0.1:$Port/api/health"
$ServerReady = $false
try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
    $ServerReady = $response.StatusCode -eq 200 -and $response.Content -match "ESPADA local evidence engine"
}
catch {
    $ServerReady = $false
}

if (-not $ServerReady) {
    $pidFile = Join-Path $ProjectRoot "work\showcase_server.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $oldPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($oldPid -match '^\d+$') {
            $oldProcess = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
            if ($oldProcess) {
                Stop-Process -Id $oldProcess.Id -Force
                Start-Sleep -Milliseconds 250
            }
        }
    }
    $serverArguments = @(
        "-m", "espada.showcase_server",
        "--root", $ProjectRoot,
        "--port", "$Port"
    )
    $server = Start-Process -FilePath $PythonPath -ArgumentList $serverArguments -WindowStyle Hidden -PassThru
    $pidDirectory = Join-Path $ProjectRoot "work"
    New-Item -ItemType Directory -Force -Path $pidDirectory | Out-Null
    Set-Content -LiteralPath (Join-Path $pidDirectory "showcase_server.pid") -Value $server.Id

    foreach ($attempt in 1..20) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match "ESPADA local evidence engine") {
                $ServerReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 150
        }
    }
}

if (-not $ServerReady) {
    throw "The local showcase server could not start on port $Port."
}

Write-Host "Open this address:" -ForegroundColor Yellow
Write-Host $ShowcaseUrl

if (-not $NoOpen) {
    try {
        Start-Process -FilePath $ShowcaseUrl
    }
    catch {
        Write-Host "Automatic browser opening was blocked. Copy the address above into Chrome or Edge." -ForegroundColor Yellow
    }
}
