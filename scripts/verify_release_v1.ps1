param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ReleaseName = "espada-prototype-v1.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReleaseRoot = Join-Path $ProjectRoot "out\releases"
$ZipPath = Join-Path $ReleaseRoot ($ReleaseName + ".zip")
if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "Release ZIP does not exist: $ZipPath"
}

$VerificationRoot = Join-Path $ReleaseRoot (".verify-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $VerificationRoot | Out-Null
try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $VerificationRoot
    $ManifestPath = Join-Path $VerificationRoot "release_manifest.json"
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Release manifest is missing from the ZIP."
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($Manifest.status -ne "PASS" -or $Manifest.public_site_modified -ne $false) {
        throw "Release manifest does not describe a passed isolated build."
    }
    foreach ($Property in $Manifest.files.PSObject.Properties) {
        $File = Join-Path $VerificationRoot $Property.Name
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
            throw "Manifest file is missing: $($Property.Name)"
        }
        $Actual = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne [string]$Property.Value) {
            throw "Integrity check failed: $($Property.Name)"
        }
    }
    $Index = Get-Content -LiteralPath (Join-Path $VerificationRoot "index.html") -Raw
    if ($Index -match '<base\s') { throw "Release contains an unsafe base URL." }
    foreach ($Link in @("evidence_dossier.html", "validation_scorecard.html", "real_world_validation.html")) {
        if ($Index -notmatch [regex]::Escape($Link)) { throw "Dashboard link is missing: $Link" }
        if (-not (Test-Path -LiteralPath (Join-Path $VerificationRoot $Link))) {
            throw "Dashboard target is missing: $Link"
        }
    }
    Write-Host "ESPADA RELEASE VERIFICATION PASS" -ForegroundColor Green
    Write-Host "ZIP: $ZipPath" -ForegroundColor Yellow
    $VerifiedCount = @($Manifest.files.PSObject.Properties).Count
    Write-Host ("Verified files: " + $VerifiedCount) -ForegroundColor Cyan
}
finally {
    $ResolvedVerification = [System.IO.Path]::GetFullPath($VerificationRoot)
    $ResolvedReleaseRoot = [System.IO.Path]::GetFullPath($ReleaseRoot)
    if ($ResolvedVerification.StartsWith($ResolvedReleaseRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        Remove-Item -LiteralPath $ResolvedVerification -Recurse -Force -ErrorAction SilentlyContinue
    }
}
