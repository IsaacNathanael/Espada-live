param(
    [string]$GpuPythonPath = "",
    [int]$BatchSize = 4,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $GpuPythonPath) { $GpuPythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $GpuPythonPath)) { throw "GPU Python missing: $GpuPythonPath" }
if ($BatchSize -lt 1) { throw "BatchSize must be positive." }
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$Dataset = Join-Path $ProjectRoot "data\datasets\dartis_external"
$BaselineCheckpoint = Join-Path $ProjectRoot "out\ml_training_v6\sar_segmentation_best.pt"
$Calibration = Join-Path $ProjectRoot "out\ml_calibration_v6\threshold_calibration.json"
if (-not $SetupOnly) {
    foreach ($Required in @((Join-Path $Dataset "external_manifest.csv"), (Join-Path $Dataset "download_status.json"), $BaselineCheckpoint, $Calibration)) {
        if (-not (Test-Path -LiteralPath $Required)) { throw "Required comparison input missing: $Required" }
    }
    & $GpuPythonPath -c "import csv,json,sys; from pathlib import Path; from espada.ml_external_eval import _verify_locked_dataset; r=Path(sys.argv[1]); p=r/'external_manifest.csv'; rows=list(csv.DictReader(p.open(encoding='utf-8-sig',newline=''))); _verify_locked_dataset(r,p,rows,json.loads((r/'download_status.json').read_text(encoding='utf-8'))); print('All 100 audit images verified.')" $Dataset
    if ($LASTEXITCODE -ne 0) { throw "Audit integrity check failed. Rerun scripts\download_dartis_external.ps1 before comparing." }
}

# Pin already-working torch/torchvision/numpy so pip cannot replace the CUDA stack.
$VersionJson = & $GpuPythonPath -c "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ['torch','torchvision','numpy']}))"
if ($LASTEXITCODE -ne 0) { throw "Working torch, torchvision and numpy are required." }
$Versions = $VersionJson | ConvertFrom-Json
Write-Host "Preparing the comparison dependencies (existing CUDA packages are pinned)..."
& $GpuPythonPath -m pip install "torch==$($Versions.torch)" "torchvision==$($Versions.torchvision)" "numpy==$($Versions.numpy)" "segmentation-models-pytorch==0.5.0" "timm==1.0.19"
if ($LASTEXITCODE -ne 0) { throw "Comparison dependency setup failed." }
& $GpuPythonPath -c "import torch,cv2,segmentation_models_pytorch as s; m=s.Unet(encoder_name='mit_b2',encoder_weights=None,in_channels=3,classes=5); print({'setup':'PASS','cuda':torch.cuda.is_available()})"
if ($LASTEXITCODE -ne 0) { throw "Model architecture check failed." }

$SpecJson = & $GpuPythonPath -c "import json; from espada.poseatsea import MODEL_SPEC; print(json.dumps(MODEL_SPEC))"
if ($LASTEXITCODE -ne 0) { throw "Could not read pinned model metadata." }
$Spec = $SpecJson | ConvertFrom-Json
$ModelDir = Join-Path $ProjectRoot "data\models\poseatsea"
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
$Checkpoint = Join-Path $ModelDir $Spec.filename
$Partial = "$Checkpoint.part"
if (-not (Test-Path -LiteralPath $Checkpoint)) {
    Write-Host "Downloading the 110 MB POSEatSea checkpoint; rerun to resume a partial transfer..."
    & curl.exe --location --fail --retry 3 --retry-all-errors --continue-at - --output $Partial $Spec.download_url
    if ($LASTEXITCODE -ne 0) { throw "Download interrupted. Rerun this command to resume." }
    $Digest = (Get-FileHash -LiteralPath $Partial -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Digest -ne $Spec.sha256 -or (Get-Item -LiteralPath $Partial).Length -ne $Spec.bytes) {
        throw "Checkpoint checksum failed. Partial file preserved for inspection: $Partial"
    }
    Move-Item -LiteralPath $Partial -Destination $Checkpoint
}
& $GpuPythonPath -c "import sys; from pathlib import Path; from espada.poseatsea import verify_checkpoint; verify_checkpoint(Path(sys.argv[1])); print('Checkpoint verified.')" $Checkpoint
if ($LASTEXITCODE -ne 0) { throw "Existing checkpoint is not the pinned model." }
if ($SetupOnly) { Write-Host "SETUP READY. Run without -SetupOnly to evaluate."; exit 0 }

$ComparisonDir = Join-Path $ProjectRoot "out\model_comparison"
# Both models rerun under the corrected identical object matcher. Original V6 audit is preserved.
Write-Host "Evaluating POSEatSea on the 100 locked images..."
& $GpuPythonPath -m espada.ml_external_eval --backend poseatsea --dataset-root $Dataset --checkpoint $Checkpoint --out (Join-Path $ComparisonDir "poseatsea") --batch-size $BatchSize
if ($LASTEXITCODE -ne 0) { throw "POSEatSea evaluation failed." }
Write-Host "Replaying V6 with the same corrected evaluator..."
& $GpuPythonPath -m espada.ml_external_eval --backend v6 --dataset-root $Dataset --checkpoint $BaselineCheckpoint --calibration $Calibration --out (Join-Path $ComparisonDir "v6") --batch-size $BatchSize
if ($LASTEXITCODE -ne 0) { throw "V6 evaluation failed. POSEatSea results were saved." }
& $GpuPythonPath -m espada.model_comparison --baseline (Join-Path $ComparisonDir "v6\external_evaluation.json") --challenger (Join-Path $ComparisonDir "poseatsea\external_evaluation.json") --out $ComparisonDir
if ($LASTEXITCODE -ne 0) { throw "Comparison report failed." }
Write-Host "COMPARISON READY: $ComparisonDir\comparison.html"
