param(
    [int]$Epochs = 20,
    [int]$BatchSize = 8,
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
if (-not (Test-Path -LiteralPath $Manifest)) {
    throw "Dataset manifest not found. Run .\scripts\audit_oil_dataset.ps1 first."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
if ($Smoke) { $Epochs = 1 }
$Arguments = @(
    "-m", "espada.ml_train",
    "--dataset-root", $DatasetRoot,
    "--manifest", $Manifest,
    "--out", (Join-Path $ProjectRoot "out\ml_training"),
    "--epochs", $Epochs,
    "--batch-size", $BatchSize
)
if ($Smoke) {
    $Arguments += @(
        "--no-pretrained",
        "--max-train-patches", "64",
        "--max-validation-patches", "32"
    )
}

& $PythonPath @Arguments
exit $LASTEXITCODE
