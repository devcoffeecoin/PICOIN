param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 8000,
  [switch]$Reload
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Python = if ($env:PICOIN_PYTHON) { $env:PICOIN_PYTHON } else { "python" }
$ArgsList = @("-m", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", "$Port")
if ($Reload) {
  $ArgsList += "--reload"
}

& $Python @ArgsList
