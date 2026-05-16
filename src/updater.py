from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .build_info import APP_VERSION, BUILD_COMMIT, UPDATE_OWNER, UPDATE_RELEASE_TAG, UPDATE_REPO, UPDATE_ZIP_ASSET


@dataclass(frozen=True)
class UpdateManifest:
    tag_name: str
    build_commit: str
    zip_asset: str = UPDATE_ZIP_ASSET
    app_version: str = APP_VERSION
    title: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    available: bool
    message: str
    manifest: UpdateManifest | None = None


def should_install_update(local_commit: str, manifest: UpdateManifest | None) -> bool:
    if manifest is None:
        return False
    remote_commit = manifest.build_commit.strip()
    local = local_commit.strip()
    if not remote_commit or not local:
        return False
    return remote_commit != local


def read_update_manifest_from_text(text: str) -> UpdateManifest:
    text = text.lstrip("\ufeff")
    data = json.loads(text)
    return UpdateManifest(
        tag_name=str(data.get("tag_name", UPDATE_RELEASE_TAG)),
        build_commit=str(data.get("build_commit", "")),
        zip_asset=str(data.get("zip_asset", UPDATE_ZIP_ASSET)),
        app_version=str(data.get("app_version", APP_VERSION)),
        title=str(data.get("title", "")),
    )


def check_for_update() -> UpdateCheckResult:
    manifest = _read_manifest_with_gh() or _read_manifest_with_api()
    if manifest is None:
        return UpdateCheckResult(False, "업데이트 정보를 찾지 못했습니다")
    if not should_install_update(BUILD_COMMIT, manifest):
        return UpdateCheckResult(False, f"최신 버전입니다 ({APP_VERSION}, {BUILD_COMMIT})", manifest)
    return UpdateCheckResult(
        True,
        f"새 버전이 있습니다: {manifest.app_version} / {manifest.build_commit}",
        manifest,
    )


def install_update_and_restart(app_dir: Path, manifest: UpdateManifest) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="kiwoom_update_"))
    zip_path = temp_dir / manifest.zip_asset
    if not _download_asset_with_gh(manifest, temp_dir):
        _download_asset_with_api(manifest, zip_path)
    if not zip_path.exists():
        raise RuntimeError(f"업데이트 zip 다운로드 실패: {manifest.zip_asset}")

    script_path = temp_dir / "apply_update.ps1"
    exe_path = app_dir / "KiwoomGlobalTraderConsole.exe"
    script_path.write_text(
        _update_script(
            pid=os.getpid(),
            zip_path=zip_path,
            app_dir=app_dir,
            exe_path=exe_path,
        ),
        encoding="utf-8",
    )
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def maybe_auto_update(app_dir: Path, progress: Callable[[str], None] | None = None) -> tuple[bool, str]:
    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    if not getattr(sys, "frozen", False):
        return False, "소스 실행 모드에서는 자동 업데이트를 건너뜁니다"
    try:
        report("업데이트 확인 중...")
        result = check_for_update()
        if not result.available or result.manifest is None:
            report(result.message)
            return False, result.message
        report("업데이트 다운로드 및 적용 준비 중...")
        install_update_and_restart(app_dir, result.manifest)
        report("업데이트 적용 중입니다. 잠시 후 콘솔을 다시 시작합니다.")
        return True, result.message + " - 업데이트 후 다시 시작합니다"
    except Exception as exc:
        return False, f"자동 업데이트 실패: {exc}"


def _update_script(pid: int, zip_path: Path, app_dir: Path, exe_path: Path) -> str:
    return f"""
$ErrorActionPreference = "Stop"
$pidToWait = {pid}
$zipPath = "{_ps_escape(zip_path)}"
$appDir = "{_ps_escape(app_dir)}"
$exePath = "{_ps_escape(exe_path)}"
$stageDir = Join-Path (Split-Path -Parent $zipPath) "stage"
$logDir = Join-Path $appDir "logs"
$logPath = Join-Path $logDir "update.log"

function Write-UpdateLog($message) {{
    try {{
        if (-not (Test-Path -LiteralPath $logDir)) {{
            New-Item -ItemType Directory -Path $logDir | Out-Null
        }}
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value ("$(Get-Date -Format s) " + $message)
    }} catch {{}}
}}

try {{
    Write-UpdateLog "waiting for pid $pidToWait"
    Wait-Process -Id $pidToWait -Timeout 60 -ErrorAction SilentlyContinue
}} catch {{}}

try {{
    Start-Sleep -Seconds 1
    Write-UpdateLog "extracting update zip"
    if (Test-Path -LiteralPath $stageDir) {{
        Remove-Item -LiteralPath $stageDir -Recurse -Force
    }}
    New-Item -ItemType Directory -Path $stageDir | Out-Null
    Expand-Archive -LiteralPath $zipPath -DestinationPath $stageDir -Force

    $internalDir = Join-Path $appDir "_internal"
    if (Test-Path -LiteralPath $internalDir) {{
        Write-UpdateLog "removing old _internal"
        Remove-Item -LiteralPath $internalDir -Recurse -Force
    }}

    $preserveNames = @("data", "logs", "config.live.json", ".env", "credentials.json")
    Get-ChildItem -LiteralPath $stageDir -Force | ForEach-Object {{
        if ($preserveNames -contains $_.Name) {{
            Write-UpdateLog ("preserve user file/folder " + $_.Name)
        }} else {{
            Copy-Item -LiteralPath $_.FullName -Destination $appDir -Recurse -Force
        }}
    }}

    $runtimeDll = Get-ChildItem -Path (Join-Path $appDir "_internal\\python*.dll") -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $runtimeDll) {{
        throw "updated _internal folder is missing python runtime dll"
    }}
    if (-not (Test-Path -LiteralPath $exePath)) {{
        throw "updated console exe is missing"
    }}

    Write-UpdateLog "update installed; restarting console"
    Start-Process -FilePath $exePath -WorkingDirectory $appDir
}} catch {{
    Write-UpdateLog ("update failed: " + $_.Exception.Message)
    throw
}}
"""


def _ps_escape(path: Path) -> str:
    return str(path).replace('"', '`"')


def _read_manifest_with_gh() -> UpdateManifest | None:
    if shutil.which("gh") is None:
        return None
    with tempfile.TemporaryDirectory(prefix="kiwoom_update_manifest_") as tmp:
        temp_dir = Path(tmp)
        result = subprocess.run(
            [
                "gh",
                "release",
                "download",
                UPDATE_RELEASE_TAG,
                "--repo",
                f"{UPDATE_OWNER}/{UPDATE_REPO}",
                "--pattern",
                "update.json",
                "--dir",
                str(temp_dir),
                "--clobber",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        manifest_path = temp_dir / "update.json"
        if result.returncode != 0 or not manifest_path.exists():
            return None
        return read_update_manifest_from_text(manifest_path.read_text(encoding="utf-8-sig"))


def _download_asset_with_gh(manifest: UpdateManifest, destination_dir: Path) -> bool:
    if shutil.which("gh") is None:
        return False
    result = subprocess.run(
        [
            "gh",
            "release",
            "download",
            manifest.tag_name,
            "--repo",
            f"{UPDATE_OWNER}/{UPDATE_REPO}",
            "--pattern",
            manifest.zip_asset,
            "--dir",
            str(destination_dir),
            "--clobber",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return result.returncode == 0 and (destination_dir / manifest.zip_asset).exists()


def _read_manifest_with_api() -> UpdateManifest | None:
    try:
        release = _read_release_json(UPDATE_RELEASE_TAG)
    except Exception:
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == "update.json":
            try:
                text = _read_url_text(str(asset["browser_download_url"]))
                return read_update_manifest_from_text(text)
            except Exception:
                return None
    return None


def _download_asset_with_api(manifest: UpdateManifest, destination: Path) -> None:
    release = _read_release_json(manifest.tag_name)
    for asset in release.get("assets", []):
        if asset.get("name") == manifest.zip_asset:
            _download_url(str(asset["browser_download_url"]), destination)
            return
    raise RuntimeError(f"업데이트 파일을 찾지 못했습니다: {manifest.zip_asset}")


def _read_release_json(tag_name: str) -> dict[str, object]:
    url = f"https://api.github.com/repos/{UPDATE_OWNER}/{UPDATE_REPO}/releases/tags/{tag_name}"
    return json.loads(_read_url_text(url))


def _request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": "KiwoomGlobalTrader"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _read_url_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=30) as response:
        return response.read().decode("utf-8-sig")


def _download_url(url: str, destination: Path) -> None:
    try:
        with urllib.request.urlopen(_request(url), timeout=120) as response:
            destination.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"업데이트 다운로드 실패: HTTP {exc.code}") from exc
