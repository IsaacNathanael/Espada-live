param(
    [int]$TrainPerSubset = 60,
    [int]$ValidationPerSubset = 20,
    [string]$PythonPath = "",
    [switch]$PlanOnly
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
$Dataset = Join-Path $ProjectRoot "data\datasets\dartis_external"
$PlanDirectory = Join-Path $ProjectRoot "out\dartis_data_plan"
$Plan = Join-Path $PlanDirectory "partition_plan.json"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

& $PythonPath -m espada.dartis_split `
    --metadata (Join-Path $Dataset "DARTIS_2019.tab") `
    --audit (Join-Path $Dataset "external_manifest.csv") `
    --out $PlanDirectory
if ($LASTEXITCODE -ne 0) { throw "Partition planning failed." }

$PilotArgs = @(
    "-m", "espada.dartis_pilot",
    "--plan", $Plan,
    "--out", (Join-Path $ProjectRoot "data\datasets\dartis_pilot"),
    "--train-per-subset", $TrainPerSubset,
    "--validation-per-subset", $ValidationPerSubset
)
if ($PlanOnly) { $PilotArgs += "--plan-only" }
& $PythonPath @PilotArgs
exit $LASTEXITCODE
