param(
  [switch]$KeepIdentities
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Python = if ($env:PICOIN_PYTHON) { $env:PICOIN_PYTHON } else { "python" }
$ArgsList = @("-m", "app.tools.reset_testnet", "--yes")
if ($KeepIdentities) {
  $ArgsList += "--keep-identities"
}

& $Python @ArgsList
