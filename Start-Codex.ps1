$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'LocalCodex.ps1')
exit $LASTEXITCODE
