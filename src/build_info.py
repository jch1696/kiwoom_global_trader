from __future__ import annotations

APP_VERSION = "v0.1.1"
BUILD_COMMIT = "dev"
UPDATE_OWNER = "jch1696"
UPDATE_REPO = "kiwoom_global_trader"
UPDATE_RELEASE_TAG = "auto-latest"
UPDATE_ZIP_ASSET = "KiwoomGlobalTraderConsole.zip"

try:
    from ._generated_build_info import (  # type: ignore
        APP_VERSION as _APP_VERSION,
        BUILD_COMMIT as _BUILD_COMMIT,
        UPDATE_OWNER as _UPDATE_OWNER,
        UPDATE_REPO as _UPDATE_REPO,
        UPDATE_RELEASE_TAG as _UPDATE_RELEASE_TAG,
        UPDATE_ZIP_ASSET as _UPDATE_ZIP_ASSET,
    )

    APP_VERSION = _APP_VERSION
    BUILD_COMMIT = _BUILD_COMMIT
    UPDATE_OWNER = _UPDATE_OWNER
    UPDATE_REPO = _UPDATE_REPO
    UPDATE_RELEASE_TAG = _UPDATE_RELEASE_TAG
    UPDATE_ZIP_ASSET = _UPDATE_ZIP_ASSET
except Exception:
    pass
