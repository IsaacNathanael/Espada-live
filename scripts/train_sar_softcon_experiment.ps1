param(
    [int]$Epochs = 40,
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
$DatasetRoot = Join-Path $ProjectRoot "data\datasets\oil_spill_zenodo_4672426\extracted"
$Manifest = Join-Path $ProjectRoot "out\ml_dataset\scene_manifest.csv"
$Encoder = Join-Path $ProjectRoot "data\models\resnet50_sentinel1_softcon.pth"
foreach ($RequiredPath in @($PythonPath, $DatasetRoot, $Manifest, $Encoder)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required SoftCon experiment input not found: $RequiredPath"
    }
}

$OutputVersion = if ($Smoke) { "v6_softcon_smoke" } else { "v6_softcon" }
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
    "--encoder-freeze-epochs", "4",
    "--encoder-learning-rate-multiplier", "0.1",
    "--encoder", "resnet50",
    "--encoder-initialization", "SoftCon Sentinel-1 SAR",
    "--encoder-checkpoint", $Encoder,
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
