param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $Destination = Join-Path $ProjectRoot "data\models\resnet50_sentinel1_all_moco-906e4356.pth"
}
$ExpectedSha256 = "906e4356da0ff06ce0388cba50dd4f310ba6abbee706c5303c129d8aead98082"
$Url = "https://hf.co/torchgeo/resnet50_sentinel1_all_moco/resolve/e79862c667853c10a709bdd77ea8ffbad0e0f1cf/resnet50_sentinel1_all_moco-906e4356.pth"
$Parent = Split-Path -Parent $Destination
New-Item -ItemType Directory -Force -Path $Parent | Out-Null

if (Test-Path -LiteralPath $Destination) {
    $ExistingHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    if ($ExistingHash -eq $ExpectedSha256) {
        @{status="PASS"; reused=$true; file=(Resolve-Path $Destination).Path; sha256=$ExistingHash} | ConvertTo-Json
        exit 0
    }
}

Write-Host "Downloading the 94.3 MB official SSL4EO-S12 Sentinel-1 encoder (safe to resume)..."
& curl.exe --location --fail --retry 5 --retry-all-errors --continue-at - --output $Destination $Url
if ($LASTEXITCODE -ne 0) { throw "Encoder download failed. Rerun this command to resume." }
$ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
if ($ActualHash -ne $ExpectedSha256) {
    throw "Encoder checksum mismatch. Expected $ExpectedSha256 but received $ActualHash."
}
@{
    status = "PASS"
    provider = "TorchGeo / SSL4EO-S12"
    pretraining = "MoCo on global dual-polarization Sentinel-1; ESPADA adapts the VV channel"
    license = "CC BY 4.0"
    file = (Resolve-Path $Destination).Path
    sha256 = $ActualHash
} | ConvertTo-Json
