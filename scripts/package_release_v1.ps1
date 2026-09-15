param(
    [string]$PythonPath = "",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ReleaseName = "espada-prototype-v1.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

if (-not $PythonPath) {
    foreach ($Candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) {
            $PythonPath = $Candidate
            break
        }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$Evidence = [ordered]@{
    "evidence_dossier.html" = Join-Path $ProjectRoot "out\challenge\dossier\evidence_dossier.html"
    "validation_scorecard.html" = Join-Path $ProjectRoot "out\system_validation\system_scorecard.html"
    "real_world_validation.html" = Join-Path $ProjectRoot "out\validation_showcase\real_world_validation.html"
}
foreach ($Item in $Evidence.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $Item.Value)) {
        throw "Required release evidence is missing: $($Item.Value)"
    }
}

# This release path is deliberately separate from docs\prototype and GitHub Pages.
$ReleaseRoot = Join-Path $ProjectRoot "out\releases"
$PackageDirectory = Join-Path $ReleaseRoot $ReleaseName
$Dashboard = Join-Path $PackageDirectory "index.html"
$ManifestPath = Join-Path $PackageDirectory "release_manifest.json"
$ReadmePath = Join-Path $PackageDirectory "START_HERE.txt"
$ZipPath = Join-Path $ReleaseRoot ($ReleaseName + ".zip")
New-Item -ItemType Directory -Force -Path $PackageDirectory | Out-Null

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.operations_dashboard `
    --project-root $ProjectRoot `
    --output $Dashboard `
    --dossier-href "evidence_dossier.html"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

foreach ($Item in $Evidence.GetEnumerator()) {
    Copy-Item -LiteralPath $Item.Value -Destination (Join-Path $PackageDirectory $Item.Key) -Force
}

$DashboardText = Get-Content -Raw -LiteralPath $Dashboard
if ($DashboardText -match '<base\s') {
    throw "The isolated release must not contain a public-site base URL."
}
foreach ($RelativeLink in $Evidence.Keys) {
    if ($DashboardText -notmatch [regex]::Escape($RelativeLink)) {
        throw "Dashboard does not link to packaged evidence: $RelativeLink"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory $RelativeLink))) {
        throw "Packaged evidence is missing: $RelativeLink"
    }
}

@"
ESPADA PROTOTYPE V1.0

1. Extract the ZIP before opening it.
2. Open index.html in a modern browser.
3. Use Run attribution for the saved-evidence replay.
4. Open the dossier and validation views from inside the dashboard.

This portable release is separate from the public GitHub Pages website.
Fresh case inference requires the local ESPADA Python engine.
"@ | Set-Content -LiteralPath $ReadmePath -Encoding UTF8

$Files = @("index.html", "START_HERE.txt") + @($Evidence.Keys)
$Hashes = [ordered]@{}
foreach ($File in $Files) {
    $Hashes[$File] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PackageDirectory $File)).Hash.ToLowerInvariant()
}

$Manifest = [ordered]@{
    status = "PASS"
    release = $ReleaseName
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    entrypoint = "index.html"
    mode = "portable saved-evidence replay"
    public_site_modified = $false
    requirements = "modern browser only"
    fresh_inference = "requires the local Python evidence engine"
    files = $Hashes
}
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $PackageDirectory "*") -DestinationPath $ZipPath -CompressionLevel Optimal

Write-Host "ESPADA FROZEN RELEASE READY" -ForegroundColor Green
Write-Host "Folder: $PackageDirectory" -ForegroundColor Yellow
Write-Host "ZIP:    $ZipPath" -ForegroundColor Yellow
Write-Host "Public GitHub Pages package was not modified." -ForegroundColor Cyan
