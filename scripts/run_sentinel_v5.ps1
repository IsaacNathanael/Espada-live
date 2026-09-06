param(
    [switch]$AnalystApproved,
    [int]$BatchSize = 4,
    [string]$GpuPythonPath = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $GpuPythonPath) {
    $GpuPythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
}
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    $PythonPath = Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $GpuPythonPath)) {
    throw "The GPU PyTorch environment was not found at '$GpuPythonPath'."
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada reporting environment was not found at '$PythonPath'."
}

$CaseRoot = Join-Path $ProjectRoot "out\sentinel1_case"
$StatusPath = Join-Path $CaseRoot "sentinel1_subset_status.json"
$ImagePath = Join-Path $CaseRoot "sentinel1_vv.tif"
$Checkpoint = Join-Path $ProjectRoot "out\ml_training_v5\sar_segmentation_best.pt"
$Calibration = Join-Path $ProjectRoot "out\ml_calibration_v5\threshold_calibration.json"
foreach ($RequiredPath in @($StatusPath, $ImagePath, $Checkpoint, $Calibration)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required V5 inference input is missing: $RequiredPath"
    }
}

$Status = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
$Output = Join-Path $CaseRoot "segmentation_v5"
$PredictionBundle = Join-Path $Output "v5_prediction.npz"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $GpuPythonPath -m espada.ml_predict `
    --input $ImagePath `
    --checkpoint $Checkpoint `
    --calibration $Calibration `
    --output $PredictionBundle `
    --batch-size $BatchSize
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Arguments = @(
    "-m", "espada.cli", "sar",
    "--input", $ImagePath,
    "--out", $Output,
    "--observation-time", $Status.acquisition_time_utc,
    "--bbox", $Status.bbox[0], $Status.bbox[1], $Status.bbox[2], $Status.bbox[3],
    "--prediction-bundle", $PredictionBundle
)
if ($AnalystApproved) { $Arguments += "--analyst-approved" }
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath @Arguments
exit $LASTEXITCODE
