param(
    [int]$PerSubset = 25,
    [int]$Seed = 26143,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    $Candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate) { $PythonPath = $Candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.dartis `
    --out (Join-Path $ProjectRoot "data\datasets\dartis_external") `
    --per-subset $PerSubset `
    --seed $Seed
exit $LASTEXITCODE
