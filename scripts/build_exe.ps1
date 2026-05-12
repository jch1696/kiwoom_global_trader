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

Write-Host "[build] building executable"
python -m PyInstaller packaging\KiwoomGlobalTraderConsole.spec --noconfirm --clean

$ExePath = Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\KiwoomGlobalTraderConsole.exe"
$CliPath = Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\KiwoomGlobalTraderCli.exe"
if (-not (Test-Path $ExePath)) {
    throw "실행파일 생성 실패: $ExePath"
}
if (-not (Test-Path $CliPath)) {
    throw "CLI 실행파일 생성 실패: $CliPath"
}

Copy-Item -LiteralPath (Join-Path $RepoRoot "README.md") -Destination (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\README.md") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "config.example.json") -Destination (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\config.example.json") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "requirements.txt") -Destination (Join-Path $RepoRoot "dist\KiwoomGlobalTraderConsole\requirements.txt") -Force

Write-Host "[build] done"
Write-Host "EXE: $ExePath"
Write-Host "CLI: $CliPath"
