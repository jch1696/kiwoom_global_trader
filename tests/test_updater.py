from __future__ import annotations

import json
import unittest

from src.updater import UpdateManifest, read_update_manifest_from_text, should_install_update


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


if __name__ == "__main__":
    unittest.main()
