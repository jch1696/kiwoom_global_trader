from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .build_info import APP_VERSION, BUILD_COMMIT, UPDATE_OWNER, UPDATE_RELEASE_TAG, UPDATE_REPO, UPDATE_ZIP_ASSET

UPDATE_RELEASE_URL = f"https://github.com/{UPDATE_OWNER}/{UPDATE_REPO}/releases/tag/{UPDATE_RELEASE_TAG}"


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
    manifest = _read_manifest_with_gh() or _read_manifest_with_api() or _read_manifest_with_direct_download()
    if manifest is None:
        return UpdateCheckResult(False, "업데이트 정보를 찾지 못했습니다")
    if not should_install_update(BUILD_COMMIT, manifest):
        return UpdateCheckResult(False, f"최신 버전입니다 ({APP_VERSION}, {BUILD_COMMIT})", manifest)
    return UpdateCheckResult(True, f"새 버전이 있습니다: {manifest.app_version} / {manifest.build_commit}", manifest)


def install_update_and_restart(app_dir: Path, manifest: UpdateManifest) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="kiwoom_update_"))
    zip_path = temp_dir / manifest.zip_asset
    if not _download_asset_with_gh(manifest, temp_dir):
        if not _download_asset_with_api(manifest, zip_path):
            _download_asset_with_direct_download(manifest, zip_path)
    if not zip_path.exists():
        raise RuntimeError(f"업데이트 ZIP 다운로드 실패: {manifest.zip_asset}")

    updater_exe = _prepare_update_runner(app_dir, temp_dir)
    exe_path = app_dir / "KiwoomGlobalTraderConsole.exe"
    subprocess.Popen(
        [
            str(updater_exe),
            "--apply-update",
            "--update-pid",
            str(os.getpid()),
            "--update-zip",
            str(zip_path),
            "--update-app-dir",
            str(app_dir),
            "--update-exe",
            str(exe_path),
        ],
        **hidden_update_subprocess_kwargs(),
    )


def _find_update_runner(app_dir: Path) -> Path:
    cli_path = app_dir / "KiwoomGlobalTraderCli.exe"
    if cli_path.exists():
        return cli_path
    console_path = app_dir / "KiwoomGlobalTraderConsole.exe"
    if console_path.exists():
        return console_path
    return Path(sys.executable)


def _prepare_update_runner(app_dir: Path, temp_dir: Path) -> Path:
    updater_exe = temp_dir / "KiwoomGlobalTraderUpdater.exe"
    shutil.copy2(_find_update_runner(app_dir), updater_exe)
    internal_dir = app_dir / "_internal"
    if internal_dir.exists():
        shutil.copytree(internal_dir, temp_dir / "_internal", dirs_exist_ok=True)
    return updater_exe


def hidden_update_subprocess_kwargs() -> dict[str, object]:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


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
        report("새 버전이 있습니다. 자동 업데이트를 적용합니다...")
        install_update_and_restart(app_dir, result.manifest)
        return True, f"{result.message} - 자동 업데이트 적용 중입니다."
    except Exception as exc:
        return False, f"자동 업데이트 실패: {exc}"


def apply_update_from_argv(argv: list[str]) -> int:
    try:
        pid = int(_arg_value(argv, "--update-pid"))
        zip_path = Path(_arg_value(argv, "--update-zip"))
        app_dir = Path(_arg_value(argv, "--update-app-dir"))
        exe_path = Path(_arg_value(argv, "--update-exe"))
        apply_update_and_restart(pid, zip_path, app_dir, exe_path)
        return 0
    except Exception as exc:
        app_dir = _optional_path_arg(argv, "--update-app-dir") or Path.cwd()
        _write_update_log(app_dir, f"update failed: {exc}")
        return 1


def apply_update_and_restart(pid: int, zip_path: Path, app_dir: Path, exe_path: Path) -> None:
    _write_update_log(app_dir, f"waiting for pid {pid}")
    _wait_for_process_exit(pid, timeout_sec=60)
    time.sleep(1)

    stage_dir = zip_path.parent / "stage"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    _write_update_log(app_dir, "extracting update zip")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(stage_dir)

    internal_dir = app_dir / "_internal"
    if internal_dir.exists():
        _write_update_log(app_dir, "removing old _internal")
        shutil.rmtree(internal_dir)

    preserve_names = {"data", "logs", "config.live.json", ".env", "credentials.json"}
    for item in stage_dir.iterdir():
        destination = app_dir / item.name
        if item.name in preserve_names:
            _write_update_log(app_dir, f"preserve user file/folder {item.name}")
            continue
        if item.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)

    if not list((app_dir / "_internal").glob("python*.dll")):
        raise RuntimeError("updated _internal folder is missing python runtime dll")
    if not exe_path.exists():
        raise RuntimeError("updated console exe is missing")

    _write_update_log(app_dir, "update installed; restarting console")
    subprocess.Popen([str(exe_path)], cwd=str(app_dir), **hidden_update_subprocess_kwargs())


def _arg_value(argv: list[str], name: str) -> str:
    try:
        index = argv.index(name)
        return argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"missing required argument {name}") from exc


def _optional_path_arg(argv: list[str], name: str) -> Path | None:
    try:
        return Path(_arg_value(argv, name))
    except Exception:
        return None


def _wait_for_process_exit(pid: int, timeout_sec: int) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return
        time.sleep(0.5)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_update_subprocess_kwargs(),
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_update_log(app_dir: Path, message: str) -> None:
    try:
        log_dir = app_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "update.log").open("a", encoding="utf-8") as file:
            file.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except Exception:
        return


def _update_script(pid: int, zip_path: Path, app_dir: Path, exe_path: Path) -> str:
    return (
        "PowerShell updater is disabled. "
        f"Use --apply-update --update-pid {pid} --update-zip {zip_path} "
        f"--update-app-dir {app_dir} --update-exe {exe_path}."
    )


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
            **hidden_update_subprocess_kwargs(),
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
        **hidden_update_subprocess_kwargs(),
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


def _read_manifest_with_direct_download() -> UpdateManifest | None:
    try:
        text = _read_url_text(_release_download_url(UPDATE_RELEASE_TAG, "update.json"))
        return read_update_manifest_from_text(text)
    except Exception:
        return None


def _download_asset_with_api(manifest: UpdateManifest, destination: Path) -> bool:
    try:
        release = _read_release_json(manifest.tag_name)
        for asset in release.get("assets", []):
            if asset.get("name") == manifest.zip_asset:
                _download_url(str(asset["browser_download_url"]), destination)
                return destination.exists()
    except Exception:
        return False
    return False


def _download_asset_with_direct_download(manifest: UpdateManifest, destination: Path) -> None:
    _download_url(_release_download_url(manifest.tag_name, manifest.zip_asset), destination)


def _read_release_json(tag_name: str) -> dict[str, object]:
    url = f"https://api.github.com/repos/{UPDATE_OWNER}/{UPDATE_REPO}/releases/tags/{tag_name}"
    return json.loads(_read_url_text(url))


def _release_download_url(tag_name: str, asset_name: str) -> str:
    return f"https://github.com/{UPDATE_OWNER}/{UPDATE_REPO}/releases/download/{tag_name}/{asset_name}"


def _request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": "KiwoomGlobalTrader"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _read_url_text(url: str) -> str:
    try:
        with urllib.request.urlopen(_request(url), timeout=30) as response:
            return response.read().decode("utf-8-sig")
    except Exception as urllib_exc:
        try:
            import requests

            headers = dict(_request(url).headers)
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.content.decode("utf-8-sig")
        except Exception as requests_exc:
            raise RuntimeError(f"URL 읽기 실패: urllib={urllib_exc}; requests={requests_exc}") from requests_exc


def _download_url(url: str, destination: Path) -> None:
    try:
        with urllib.request.urlopen(_request(url), timeout=120) as response:
            destination.write_bytes(response.read())
            return
    except Exception as urllib_exc:
        try:
            import requests

            headers = dict(_request(url).headers)
            with requests.get(url, headers=headers, timeout=120, stream=True) as response:
                response.raise_for_status()
                with destination.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file.write(chunk)
            return
        except Exception as requests_exc:
            if isinstance(urllib_exc, urllib.error.HTTPError):
                raise RuntimeError(f"업데이트 다운로드 실패: HTTP {urllib_exc.code}; requests={requests_exc}") from requests_exc
            raise RuntimeError(f"업데이트 다운로드 실패: urllib={urllib_exc}; requests={requests_exc}") from requests_exc
