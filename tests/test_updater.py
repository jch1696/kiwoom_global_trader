from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.updater import (
    UpdateCheckResult,
    UpdateManifest,
    _download_asset_with_api,
    _download_url,
    _prepare_update_runner,
    _read_manifest_with_direct_download,
    _read_url_text,
    _release_download_url,
    _update_script,
    apply_update_and_restart,
    hidden_update_subprocess_kwargs,
    maybe_auto_update,
    read_update_manifest_from_text,
    should_install_update,
)


class UpdaterTest(unittest.TestCase):
    def test_reads_update_manifest(self) -> None:
        manifest = read_update_manifest_from_text(
            json.dumps(
                {
                    "tag_name": "auto-latest",
                    "app_version": "auto-7",
                    "build_commit": "abc1234",
                    "zip_asset": "KiwoomGlobalTraderConsole.zip",
                    "title": "latest",
                }
            )
        )

        self.assertEqual(manifest.tag_name, "auto-latest")
        self.assertEqual(manifest.app_version, "auto-7")
        self.assertEqual(manifest.build_commit, "abc1234")

    def test_reads_update_manifest_with_bom(self) -> None:
        manifest = read_update_manifest_from_text('\ufeff{"tag_name":"auto-latest","build_commit":"abc1234"}')

        self.assertEqual(manifest.tag_name, "auto-latest")
        self.assertEqual(manifest.build_commit, "abc1234")

    def test_should_install_update_only_when_commit_differs(self) -> None:
        self.assertFalse(should_install_update("abc1234", UpdateManifest("auto-latest", "abc1234")))
        self.assertTrue(should_install_update("abc1234", UpdateManifest("auto-latest", "def5678")))
        self.assertFalse(should_install_update("abc1234", UpdateManifest("auto-latest", "")))
        self.assertFalse(should_install_update("abc1234", None))

    def test_update_script_is_disabled_power_shell_notice(self) -> None:
        script = _update_script(
            pid=123,
            zip_path=Path(r"C:\Temp\KiwoomGlobalTraderConsole.zip"),
            app_dir=Path(r"C:\App"),
            exe_path=Path(r"C:\App\KiwoomGlobalTraderConsole.exe"),
        )

        self.assertIn("PowerShell updater is disabled", script)
        self.assertIn("--apply-update", script)
        self.assertIn("--update-zip", script)

    def test_hidden_update_subprocess_kwargs_hides_windows_console(self) -> None:
        kwargs = hidden_update_subprocess_kwargs()

        if sys.platform == "win32":
            self.assertIn("creationflags", kwargs)
            self.assertIn("startupinfo", kwargs)
        else:
            self.assertEqual(kwargs, {})

    def test_auto_update_skips_source_mode_without_progress(self) -> None:
        messages: list[str] = []
        updated, message = maybe_auto_update(Path("."), progress=messages.append)

        self.assertFalse(updated)
        self.assertIn("소스 실행 모드", message)
        self.assertEqual(messages, [])

    def test_auto_update_applies_update_when_update_exists(self) -> None:
        messages: list[str] = []
        manifest = UpdateManifest("auto-latest", "new1234", app_version="auto-20")

        with (
            patch("src.updater.sys.frozen", True, create=True),
            patch("src.updater.check_for_update", return_value=UpdateCheckResult(True, "새 버전이 있습니다: auto-20 / new1234", manifest)),
            patch("src.updater.install_update_and_restart") as install,
        ):
            updated, message = maybe_auto_update(Path("C:/App"), progress=messages.append)

        self.assertTrue(updated)
        self.assertIn("자동 업데이트 적용 중", message)
        self.assertEqual(messages[-1], "새 버전이 있습니다. 자동 업데이트를 적용합니다...")
        install.assert_called_once_with(Path("C:/App"), manifest)

    def test_prepare_update_runner_copies_internal_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / "app"
            temp_dir = root / "temp"
            (app_dir / "_internal").mkdir(parents=True)
            temp_dir.mkdir()
            (app_dir / "KiwoomGlobalTraderCli.exe").write_text("cli", encoding="utf-8")
            (app_dir / "_internal" / "python311.dll").write_text("runtime", encoding="utf-8")

            updater = _prepare_update_runner(app_dir, temp_dir)

            self.assertEqual(updater, temp_dir / "KiwoomGlobalTraderUpdater.exe")
            self.assertEqual(updater.read_text(encoding="utf-8"), "cli")
            self.assertTrue((temp_dir / "_internal" / "python311.dll").exists())

    def test_apply_update_preserves_user_files_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / "app"
            update_dir = root / "update"
            app_dir.mkdir()
            update_dir.mkdir()
            (app_dir / "_internal").mkdir()
            (app_dir / "_internal" / "python-old.dll").write_text("old", encoding="utf-8")
            (app_dir / "config.live.json").write_text("user-config", encoding="utf-8")
            zip_path = root / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("_internal/python-new.dll", "new")
                archive.writestr("KiwoomGlobalTraderConsole.exe", "exe")
                archive.writestr("KiwoomGlobalTraderCli.exe", "cli")
                archive.writestr("config.live.json", "release-config")

            with (
                patch("src.updater._wait_for_process_exit"),
                patch("src.updater.subprocess.Popen") as popen,
            ):
                apply_update_and_restart(123, zip_path, app_dir, app_dir / "KiwoomGlobalTraderConsole.exe")

            self.assertEqual((app_dir / "config.live.json").read_text(encoding="utf-8"), "user-config")
            self.assertTrue((app_dir / "_internal" / "python-new.dll").exists())
            self.assertFalse((app_dir / "_internal" / "python-old.dll").exists())
            popen.assert_called_once()

    def test_release_download_url_uses_direct_asset_url(self) -> None:
        self.assertEqual(
            _release_download_url("auto-latest", "update.json"),
            "https://github.com/jch1696/kiwoom_global_trader/releases/download/auto-latest/update.json",
        )

    def test_reads_manifest_from_direct_download_url(self) -> None:
        with patch(
            "src.updater._read_url_text",
            return_value='{"tag_name":"auto-latest","build_commit":"ab515ac","app_version":"auto-16"}',
        ) as read_url:
            manifest = _read_manifest_with_direct_download()

        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(manifest.build_commit, "ab515ac")
        read_url.assert_called_once_with(
            "https://github.com/jch1696/kiwoom_global_trader/releases/download/auto-latest/update.json"
        )

    def test_download_asset_with_api_returns_false_when_api_fails(self) -> None:
        with patch("src.updater._read_release_json", side_effect=RuntimeError("blocked")):
            self.assertFalse(
                _download_asset_with_api(
                    UpdateManifest("auto-latest", "ab515ac", "KiwoomGlobalTraderConsole.zip"),
                    Path("KiwoomGlobalTraderConsole.zip"),
                )
            )

    def test_read_url_text_falls_back_to_requests(self) -> None:
        class Response:
            content = b'{"ok": true}'

            def raise_for_status(self) -> None:
                return None

        with (
            patch("src.updater.urllib.request.urlopen", side_effect=OSError("urllib blocked")),
            patch("requests.get", return_value=Response()) as get,
        ):
            self.assertEqual(_read_url_text("https://example.com/update.json"), '{"ok": true}')

        get.assert_called_once()

    def test_download_url_falls_back_to_requests(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int):
                yield b"abc"
                yield b"def"

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "asset.zip"
            with (
                patch("src.updater.urllib.request.urlopen", side_effect=OSError("urllib blocked")),
                patch("requests.get", return_value=Response()) as get,
            ):
                _download_url("https://example.com/asset.zip", target)

            self.assertEqual(target.read_bytes(), b"abcdef")
            get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
