param(
    [int]$BatchSize = 2,
    [ValidateSet("v4", "v5")]
    [string]$Version = "v5",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $Candidates = @(
        (Join-Path $env:USERPROFILE "ml\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate) { $PythonPath = $Candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "GPU PyTorch environment was not found."
}

$DatasetRoot = Join-Path $ProjectRoot "data\datasets\oil_spill_zenodo_4672426\extracted"
$Manifest = Join-Path $ProjectRoot "out\ml_dataset\scene_manifest.csv"
$Checkpoint = Join-Path $ProjectRoot "out\ml_training_$Version\sar_segmentation_best.pt"
$Calibration = Join-Path $ProjectRoot "out\ml_calibration_$Version\threshold_calibration.json"
foreach ($RequiredPath in @($DatasetRoot, $Manifest, $Checkpoint, $Calibration)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required evaluation input not found: $RequiredPath"
    }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.ml_evaluate `
    --dataset-root $DatasetRoot `
    --manifest $Manifest `
    --checkpoint $Checkpoint `
    --calibration $Calibration `
    --out (Join-Path $ProjectRoot "out\ml_test_${Version}_development_replay") `
    --batch-size $BatchSize `
    --development-replay
exit $LASTEXITCODE
