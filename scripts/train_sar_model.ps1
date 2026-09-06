param(
    [int]$Epochs = 40,
    [int]$BatchSize = 2,
    [int]$AccumulationSteps = 4,
    [int]$EncoderFreezeEpochs = 4,
    [switch]$Smoke,
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
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "GPU PyTorch environment not found at '$PythonPath'."
}

$DatasetRoot = Join-Path $ProjectRoot "data\datasets\oil_spill_zenodo_4672426\extracted"
$Manifest = Join-Path $ProjectRoot "out\ml_dataset\scene_manifest.csv"
$EncoderCheckpoint = Join-Path $ProjectRoot "data\models\resnet50_sentinel1_all_moco-906e4356.pth"
if (-not (Test-Path -LiteralPath $Manifest)) {
    throw "Dataset manifest not found. Run .\scripts\audit_oil_dataset.ps1 first."
}
if (-not (Test-Path -LiteralPath $EncoderCheckpoint)) {
    throw "V4 Sentinel-1 encoder not found. Run .\scripts\download_sar_encoder.ps1 first."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:NO_ALBUMENTATIONS_UPDATE = "1"
if ($Smoke) { $Epochs = 1 }
$Arguments = @(
    "-m", "espada.ml_train",
    "--dataset-root", $DatasetRoot,
    "--manifest", $Manifest,
    "--out", (Join-Path $ProjectRoot "out\ml_training_v5"),
    "--epochs", $Epochs,
    "--batch-size", $BatchSize,
    "--gradient-accumulation-steps", $AccumulationSteps,
    "--encoder-freeze-epochs", $EncoderFreezeEpochs,
    "--encoder-learning-rate-multiplier", "0.1",
    "--encoder", "resnet50",
    "--encoder-checkpoint", $EncoderCheckpoint,
    "--normalization", "scene_centered_s1_vv",
    "--augmentation", "albumentations",
    "--decoder-normalization", "group"
)
if ($Smoke) {
    $Arguments += @(
        "--max-train-patches", "64",
        "--max-validation-patches", "32"
    )
}

& $PythonPath @Arguments
exit $LASTEXITCODE
