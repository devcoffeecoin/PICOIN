param(
  [string]$Server = "http://127.0.0.1:8000",
  [int]$Loops = 1,
  [double]$Sleep = 1.0
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Python = if ($env:PICOIN_PYTHON) { $env:PICOIN_PYTHON } else { "python" }

& $Python -m validator.client --server $Server --identity data/testnet/identities/validator-two.json validate --loops $Loops --sleep $Sleep
