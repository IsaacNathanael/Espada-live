param([string]$PythonPath = "")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$copernicusCache = Join-Path $ProjectRoot "data\cache\environment_copernicus.json"
$openMeteoCache = Join-Path $ProjectRoot "data\cache\environment_latest.json"
$preferredCache = if (Test-Path -LiteralPath $copernicusCache) { $copernicusCache } else { $openMeteoCache }
$slickInput = Join-Path $ProjectRoot "out\demo\slick_observation.geojson"
if (-not (Test-Path -LiteralPath $preferredCache)) { throw "No real environmental cache is available." }
if (-not (Test-Path -LiteralPath $slickInput)) { throw "Run scripts/run_demo.ps1 first." }

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath -m espada.cli slick `
    --input $slickInput `
    --environment-cache $preferredCache `
    --out (Join-Path $ProjectRoot "out\slick")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli dashboard --out (Join-Path $ProjectRoot "out\demo")
exit $LASTEXITCODE
