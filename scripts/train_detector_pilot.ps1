param(
    [int]$Epochs = 8,
    [int]$BatchSize = 2,
    [switch]$Smoke,
    [string]$GpuPythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $GpuPythonPath) { $GpuPythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $GpuPythonPath)) { throw "GPU Python missing: $GpuPythonPath" }
$Dataset = Join-Path $ProjectRoot "data\datasets\dartis_pilot"
$Manifest = Join-Path $Dataset "pilot_manifest.csv"
$Status = Join-Path $Dataset "download_status.json"
foreach ($Required in @($Manifest, $Status)) {
    if (-not (Test-Path -LiteralPath $Required)) { throw "Required pilot input missing: $Required" }
}
$OutputName = if ($Smoke) { "detector_pilot_smoke" } else { "detector_pilot" }
$Arguments = @(
    "-m", "espada.detector_pilot",
    "--manifest", $Manifest,
    "--status", $Status,
    "--out", (Join-Path $ProjectRoot "out\$OutputName"),
    "--epochs", $Epochs,
    "--batch-size", $BatchSize
)
if ($Smoke) { $Arguments += "--smoke" }
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $GpuPythonPath @Arguments
exit $LASTEXITCODE
