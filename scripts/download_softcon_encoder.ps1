param(
    [string]$Destination = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $Destination = Join-Path $ProjectRoot "data\models\resnet50_sentinel1_softcon.pth"
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "GPU PyTorch environment not found at '$PythonPath'."
}
$Url = "https://huggingface.co/wangyi111/softcon/resolve/62ff465b2e7467dbfc70758ec1e9d08ab87fc46b/B2_rn50_softcon.pth"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null

if (-not (Test-Path -LiteralPath $Destination)) {
    Write-Host "Downloading the optional official SoftCon Sentinel-1 ResNet50 encoder (safe to resume)..."
    & curl.exe --location --fail --retry 5 --retry-all-errors --continue-at - --output $Destination $Url
    if ($LASTEXITCODE -ne 0) { throw "SoftCon download failed. Rerun this command to resume." }
}
if ((Get-Item -LiteralPath $Destination).Length -lt 80000000) {
    throw "SoftCon file is unexpectedly small; delete it and rerun the download."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -c "import sys; from pathlib import Path; from espada.ml_model import _load_encoder_state; state=_load_encoder_state(Path(sys.argv[1])); print({'status':'compatible','loaded_keys':len(state),'input_channels':int(state['conv1.weight'].shape[1])})" $Destination
if ($LASTEXITCODE -ne 0) { throw "The downloaded SoftCon checkpoint is not compatible." }
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
@{
    status = "PASS"
    provider = "SoftCon official Hugging Face checkpoint"
    pretraining = "SoftCon ResNet50 on dual-polarization SSL4EO-S12 Sentinel-1"
    source = $Url
    file = (Resolve-Path $Destination).Path
    sha256 = $Hash
} | ConvertTo-Json
