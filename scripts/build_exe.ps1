$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

Write-Host "[build] installing runtime requirements"
python -m pip install -r requirements.txt

Write-Host "[build] installing build requirements"
python -m pip install -r requirements-build.txt

Write-Host "[build] cleaning old build output"
if (Test-Path build) {
    Remove-Item -LiteralPath build -Recurse -Force
}
if (Test-Path dist\KiwoomGlobalTraderConsole) {
    Remove-Item -LiteralPath dist\KiwoomGlobalTraderConsole -Recurse -Force
}
if (Test-Path dist\KiwoomGlobalTraderConsole.zip) {
    Remove-Item -LiteralPath dist\KiwoomGlobalTraderConsole.zip -Force
}
if (Test-Path dist\update.json) {
    Remove-Item -LiteralPath dist\update.json -Force
}

$BuildCommit = "dev"
try {
    $BuildCommit = (git rev-parse --short HEAD).Trim()
} catch {
    $BuildCommit = "dev"
}
$AppVersion = if ($env:APP_VERSION) { $env:APP_VERSION } else { "v0.1.1" }
$GeneratedInfo = @"
APP_VERSION = "$AppVersion"
BUILD_COMMIT = "$BuildCommit"
UPDATE_OWNER = "jch1696"
UPDATE_REPO = "kiwoom_global_trader"
UPDATE_RELEASE_TAG = "auto-latest"
UPDATE_ZIP_ASSET = "KiwoomGlobalTraderConsole.zip"
"@
$GeneratedInfo | Set-Content -LiteralPath (Join-Path $RepoRoot "src\_generated_build_info.py") -Encoding UTF8

Write-Host "[build] building executable"
python -m PyInstaller packaging\KiwoomGlobalTraderConsole.spec --noconfirm --clean

$ExePath = Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\KiwoomGlobalTraderConsole.exe"
$CliPath = Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\KiwoomGlobalTraderCli.exe"
if (-not (Test-Path $ExePath)) {
    throw "Console executable was not created: $ExePath"
}
if (-not (Test-Path $CliPath)) {
    throw "CLI executable was not created: $CliPath"
}

Copy-Item -LiteralPath (Join-Path $RepoRoot "README.md") -Destination (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\README.md") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "config.example.json") -Destination (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\config.example.json") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "requirements.txt") -Destination (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\requirements.txt") -Force

$ZipPath = Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole.zip"
Compress-Archive -Path (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\*") -DestinationPath $ZipPath -Force

$Manifest = @{
    tag_name = "auto-latest"
    app_version = $AppVersion
    build_commit = $BuildCommit
    zip_asset = "KiwoomGlobalTraderConsole.zip"
    title = "Kiwoom Global Trader auto update"
    created_at = (Get-Date).ToString("s")
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $RepoRoot "dist\update.json") -Encoding UTF8

Write-Host "[build] done"
Write-Host "EXE: $ExePath"
Write-Host "CLI: $CliPath"
Write-Host "ZIP: $ZipPath"
