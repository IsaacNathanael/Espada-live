param(
    [int]$BatchSize = 4,
    [string]$GpuPythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $GpuPythonPath) {
    $GpuPythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $GpuPythonPath)) {
    throw "The GPU PyTorch environment was not found at '$GpuPythonPath'."
}

$DatasetRoot = Join-Path $ProjectRoot "data\datasets\dartis_external"
$Checkpoint = Join-Path $ProjectRoot "out\ml_training_v6\sar_segmentation_best.pt"
$Calibration = Join-Path $ProjectRoot "out\ml_calibration_v6\threshold_calibration.json"
foreach ($RequiredPath in @(
    (Join-Path $DatasetRoot "external_manifest.csv"),
    (Join-Path $DatasetRoot "download_status.json"),
    $Checkpoint,
    $Calibration
)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required external-evaluation input is missing: $RequiredPath"
    }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $GpuPythonPath -m espada.ml_external_eval `
    --dataset-root $DatasetRoot `
    --checkpoint $Checkpoint `
    --calibration $Calibration `
    --out (Join-Path $ProjectRoot "out\ml_external_dartis_v6") `
    --batch-size $BatchSize
exit $LASTEXITCODE
