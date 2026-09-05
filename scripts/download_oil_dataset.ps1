param(
    [string]$DestinationRoot = "",
    [switch]$SkipExtract
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $DestinationRoot) {
    $DestinationRoot = Join-Path $ProjectRoot "data\datasets\oil_spill_zenodo_4672426"
}

$DatasetUrl = "https://zenodo.org/api/records/4672426/files/Radar_data.rar/content"
$ExpectedBytes = 487624870
$ExpectedMd5 = "cff59307844603e86d2b1c641d1b5a37"
$ArchivePath = Join-Path $DestinationRoot "Radar_data.rar"
$ExtractPath = Join-Path $DestinationRoot "extracted"
$StatusPath = Join-Path $DestinationRoot "dataset_status.json"

New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null

function Test-VerifiedArchive {
    if (-not (Test-Path -LiteralPath $ArchivePath)) { return $false }
    $File = Get-Item -LiteralPath $ArchivePath
    if ($File.Length -ne $ExpectedBytes) { return $false }
    $Digest = (Get-FileHash -LiteralPath $ArchivePath -Algorithm MD5).Hash.ToLowerInvariant()
    return $Digest -eq $ExpectedMd5
}

if (-not (Test-VerifiedArchive)) {
    Write-Host "Downloading the 487.6 MB labelled Sentinel-1 dataset (safe to rerun/resume)..."
    & curl.exe --location --fail --retry 5 --retry-delay 3 --continue-at - `
        --output $ArchivePath $DatasetUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Dataset download failed. Keep the partial file and rerun this command to resume."
    }
}

if (-not (Test-VerifiedArchive)) {
    $ActualBytes = if (Test-Path -LiteralPath $ArchivePath) {
        (Get-Item -LiteralPath $ArchivePath).Length
    } else { 0 }
    throw "Dataset verification failed (received $ActualBytes of $ExpectedBytes bytes, or MD5 did not match)."
}

$Extracted = $false
if (-not $SkipExtract) {
    New-Item -ItemType Directory -Force -Path $ExtractPath | Out-Null
    Write-Host "Archive checksum passed. Extracting labelled images and masks..."
    & tar.exe -xf $ArchivePath -C $ExtractPath
    if ($LASTEXITCODE -ne 0) {
        throw "Windows tar could not extract this RAR archive. Install 7-Zip, extract Radar_data.rar into '$ExtractPath', then rerun with -SkipExtract."
    }
    $Extracted = $true
}

$Status = [ordered]@{
    status = "PASS"
    dataset = "Oil Spill Segmentation"
    provider = "Zenodo"
    doi = "10.5281/zenodo.4672426"
    license = "CC BY 4.0"
    archive = $ArchivePath
    expected_bytes = $ExpectedBytes
    md5 = $ExpectedMd5
    extracted = $Extracted
    extracted_path = $ExtractPath
    notes = @(
        "Twenty-three Sentinel-1A GRD VV scenes from the Gulf of Mexico (2018-2020).",
        "Masks were prepared from NOAA high-confidence oil-spill reports.",
        "This dataset supports segmentation research; it does not prove that every dark SAR region is oil."
    )
}
$Status | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
$Status | ConvertTo-Json -Depth 5
