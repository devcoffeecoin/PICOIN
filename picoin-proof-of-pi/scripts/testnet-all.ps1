param(
  [int]$Port = 8000,
  [int]$Workers = 1,
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Python = if ($env:PICOIN_PYTHON) { $env:PICOIN_PYTHON } else { "python" }
$Server = "http://127.0.0.1:$Port"

if (-not $SkipReset) {
  & $Python -m app.tools.reset_testnet --yes
  & $Python -m app.tools.bootstrap_testnet --server $Server
}

$ServerScript = Join-Path $PSScriptRoot "testnet-server.ps1"
$Process = Start-Process -WindowStyle Hidden -PassThru powershell -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $ServerScript,
  "-Port",
  "$Port"
)

try {
  $Ready = $false
  for ($Index = 0; $Index -lt 30; $Index++) {
    try {
      Invoke-RestMethod -Uri "$Server/" -TimeoutSec 2 | Out-Null
      $Ready = $true
      break
    } catch {
      Start-Sleep -Seconds 1
    }
  }

  if (-not $Ready) {
    throw "Server did not become ready at $Server"
  }

  & $Python -m app.tools.run_testnet_cycle --server $Server --workers $Workers
} finally {
  Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
}
