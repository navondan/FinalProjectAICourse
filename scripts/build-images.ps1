# Build all three images tagged with the current commit SHA.
#   .\scripts\build-images.ps1            # refuses to build from uncommitted changes
#   .\scripts\build-images.ps1 -AllowDirty
param([switch]$AllowDirty)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$sha = (git rev-parse --short HEAD).Trim()
$dirty = git status --porcelain
if ($dirty -and -not $AllowDirty) {
    throw "Working tree has uncommitted changes; an image tagged $sha would not match that commit. Commit first or pass -AllowDirty."
}
if ($dirty) { $sha = "$sha-dirty" }

$env:GIT_SHA = $sha
docker compose build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built novaops/{mcp,api,renewal-worker}:$sha"
