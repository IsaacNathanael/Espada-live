param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $PythonPath) {
    $PythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "GPU PyTorch environment not found at '$PythonPath'."
}

Write-Host "Installing the small V4 augmentation dependency..."
& $PythonPath -m pip install "albumentations==2.0.8"
if ($LASTEXITCODE -ne 0) { throw "Albumentations installation failed." }
& $PythonPath -c "import albumentations as a; print({'status':'PASS','albumentations':a.__version__})"
