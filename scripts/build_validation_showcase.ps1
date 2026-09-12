param([string]$Output = "")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$PythonPath = ""
foreach ($Candidate in @(
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
)) {
    if (Test-Path -LiteralPath $Candidate) { $PythonPath = $Candidate; break }
}
if (-not $PythonPath) { throw "The Espada Python environment was not found." }
if (-not $Output) { $Output = Join-Path $ProjectRoot "out\validation_showcase\real_world_validation.html" }
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.validation_showcase --project-root $ProjectRoot --output $Output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
