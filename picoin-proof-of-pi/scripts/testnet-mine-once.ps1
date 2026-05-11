param(
  [string]$Server = "http://127.0.0.1:8000",
  [int]$Workers = 1
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Python = if ($env:PICOIN_PYTHON) { $env:PICOIN_PYTHON } else { "python" }

& $Python -m miner.client --server $Server --identity data/testnet/identities/miner-alice.json mine --once --workers $Workers
