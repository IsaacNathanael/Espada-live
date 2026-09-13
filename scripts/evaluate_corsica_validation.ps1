param([string]$PythonPath = "")

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

$CaseRoot = Join-Path $ProjectRoot "out\external_validation\corsica_2018\run"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.external_validation `
    --registry (Join-Path $ProjectRoot "validation\corsica_2018.json") `
    --ranking (Join-Path $CaseRoot "ranking\candidates.json") `
    --release-estimate (Join-Path $CaseRoot "drift\release_estimate.json") `
    --decision (Join-Path $CaseRoot "decision\decision_gate.json") `
    --output (Join-Path $ProjectRoot "out\external_validation\corsica_2018\validation_result.json")
exit $LASTEXITCODE
