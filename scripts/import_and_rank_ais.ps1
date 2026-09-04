param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$ResolvedInput = Resolve-Path -LiteralPath $InputPath -ErrorAction Stop
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

$ImportDirectory = Join-Path $ProjectRoot "out\ais_import"
$RankingDirectory = Join-Path $ProjectRoot "out\ais_ranking"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath -m espada.cli ais --input $ResolvedInput --out $ImportDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli rank-ais `
    --ais (Join-Path $ImportDirectory "ais_normalized.csv") `
    --case (Join-Path $ProjectRoot "out\demo") `
    --out $RankingDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "AIS import and ranking complete. Open:"
Write-Output (Join-Path $RankingDirectory "candidates.json")
