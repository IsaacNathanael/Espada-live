param(
    [int]$Port = 4173,
    [switch]$NoOpen,
    [string]$PythonPath = "",
    [string]$GpuPythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
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
if ($GpuPythonPath -and -not (Test-Path -LiteralPath $GpuPythonPath)) {
    throw "The requested GPU Python environment was not found: $GpuPythonPath"
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
if ($GpuPythonPath) { $env:ESPADA_GPU_PYTHON = $GpuPythonPath }
$Url = "http://127.0.0.1:$Port/docs/local_case_portal/index.html"
$HealthUrl = "http://127.0.0.1:$Port/api/health"
$ServerReady = $false
try {
    $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
    $ServerReady = $Health.status -eq "PASS" -and $Health.capabilities -contains "validated-case-intake"
}
catch { $ServerReady = $false }

if (-not $ServerReady) {
    $PidDirectory = Join-Path $ProjectRoot "work"
    $PidFile = Join-Path $PidDirectory "showcase_server.pid"
    if (Test-Path -LiteralPath $PidFile) {
        $OldPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($OldPid -match '^\d+$') {
            $OldProcess = Get-Process -Id ([int]$OldPid) -ErrorAction SilentlyContinue
            if ($OldProcess) { Stop-Process -Id $OldProcess.Id -Force; Start-Sleep -Milliseconds 250 }
        }
    }
    $ServerArguments = @("-m", "espada.showcase_server", "--root", $ProjectRoot, "--port", "$Port")
    $Server = Start-Process -FilePath $PythonPath -ArgumentList $ServerArguments -WindowStyle Hidden -PassThru
    New-Item -ItemType Directory -Force -Path $PidDirectory | Out-Null
    Set-Content -LiteralPath $PidFile -Value $Server.Id
    foreach ($Attempt in 1..30) {
        try {
            $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
            if ($Health.status -eq "PASS" -and $Health.capabilities -contains "validated-case-intake") {
                $ServerReady = $true
                break
            }
        }
        catch { Start-Sleep -Milliseconds 200 }
    }
}
if (-not $ServerReady) { throw "The local case portal could not start on port $Port." }

Write-Host "ESPADA LOCAL CASE PORTAL READY" -ForegroundColor Green
Write-Host $Url -ForegroundColor Yellow
Write-Host "This operator portal is separate from the public website." -ForegroundColor Cyan
if (-not $NoOpen) {
    try { Start-Process -FilePath $Url }
    catch { Write-Host "Copy the address above into Chrome or Edge." -ForegroundColor Yellow }
}
