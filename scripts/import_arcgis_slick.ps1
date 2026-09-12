param(
    [Parameter(Mandatory = $true)][string]$LayerUrl,
    [Parameter(Mandatory = $true)][string]$ObservationTimeUtc,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Output,
    [double]$Confidence = 0.70,
    [string]$ReviewStatus = "external_expert_mapping",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    foreach ($Candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) { $PythonPath = $Candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.arcgis_slick `
    --layer-url $LayerUrl `
    --observation-time $ObservationTimeUtc `
    --source $Source `
    --output $Output `
    --confidence $Confidence `
    --review-status $ReviewStatus
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
