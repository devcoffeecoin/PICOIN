param(
  [string]$Server = "http://127.0.0.1:8000",
  [double]$MinerFaucet = 31.416
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Python = if ($env:PICOIN_PYTHON) { $env:PICOIN_PYTHON } else { "python" }

& $Python -m app.tools.bootstrap_testnet --server $Server --miner-faucet $MinerFaucet
