param(
    [int]$Epochs = 15,
    [int]$BatchSize = 2,
    [int]$AccumulationSteps = 4,
    [switch]$Smoke,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "GPU PyTorch environment not found at '$PythonPath'."
}

$DatasetRoot = Join-Path $ProjectRoot "data\datasets\oil_spill_zenodo_4672426\extracted"
$Manifest = Join-Path $ProjectRoot "out\ml_dataset\scene_manifest.csv"
$V5Checkpoint = Join-Path $ProjectRoot "out\ml_training_v5\sar_segmentation_best.pt"
foreach ($RequiredPath in @($DatasetRoot, $Manifest, $V5Checkpoint)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required V6 input not found: $RequiredPath"
    }
}

$OutputVersion = if ($Smoke) { "v6_smoke" } else { "v6" }
if ($Smoke) { $Epochs = 1 }
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:NO_ALBUMENTATIONS_UPDATE = "1"
$Arguments = @(
    "-m", "espada.ml_train",
    "--dataset-root", $DatasetRoot,
    "--manifest", $Manifest,
    "--out", (Join-Path $ProjectRoot "out\ml_training_$OutputVersion"),
    "--epochs", $Epochs,
    "--batch-size", $BatchSize,
    "--gradient-accumulation-steps", $AccumulationSteps,
    "--encoder-freeze-epochs", "0",
    "--encoder-learning-rate-multiplier", "0.1",
    "--encoder", "resnet50",
    "--encoder-initialization", "V5 SSL4EO-S12 MoCo warm start",
    "--initial-checkpoint", $V5Checkpoint,
    "--learning-rate", "0.00005",
    "--patience", "6",
    "--normalization", "scene_centered_s1_vv",
    "--augmentation", "albumentations",
    "--augmentation-profile", "sar_v6",
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
