param(
    [string]$PythonPath = "",
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Arguments = @{
    CaseId = "corsica_2018"
    ObservationTimeUtc = "2018-10-08T05:28:00Z"
    MinLongitude = 8.95
    MinLatitude = 43.0
    MaxLongitude = 9.75
    MaxLatitude = 43.6
    TargetLongitude = 9.475
    TargetLatitude = 43.246667
    SearchWindowHours = 8
    MaximumSlickAgeHours = 30
    DatasetId = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
    OutputDirectory = (Join-Path $ProjectRoot "out\external_validation\corsica_2018")
}
if ($PythonPath) { $Arguments.PythonPath = $PythonPath }
if ($PlanOnly) { $Arguments.PlanOnly = $true }

& (Join-Path $PSScriptRoot "assemble_incident.ps1") @Arguments
exit $LASTEXITCODE
