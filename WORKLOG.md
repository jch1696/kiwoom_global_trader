# Work Log

## 2026-05-20

### Kiwoom Global Trader Console local fixes

- Confirmed live order button clicking works again after restoring mouse-coordinate click as the first order-button method.
- Fixed live order CLI commands so `--place-order`, `--place-decision-order`, and `--cancel-order` force live mode even when the loaded config has `trading.dry_run=true`.
- Fixed rebalance order pricing:
  - Rebalance buy/sell orders now use the HTS current price first so the account balance can be adjusted to the current tier quantity.
  - Tier buy/sell prices are used only as a fallback when current price is unavailable.
- Fixed post-order verification for current-price orders:
  - Previously, after clicking the order button and confirming the HTS popup, the order was marked failed if a matching open order was not found.
  - Current-price orders can fill immediately and disappear from open orders, so this case is now treated as success unless an HTS rejection/info popup is detected.
- Rebuilt the normal console at `dist/KiwoomGlobalTraderConsole/KiwoomGlobalTraderConsole.exe`.
- Restored `config.live.json`, `data`, and `.env` into the rebuilt `dist/KiwoomGlobalTraderConsole` folder.
- Verification:
  - `python -m pytest` passed with `134 passed, 1 skipped`.
  - User confirmed the live order test succeeded.
- Follow-up:
  - ETHT showed `ORDER OK` while open-order verification had actually failed with `HTS main window is not open`.
  - Tightened post-order verification so "missing from open orders" is treated as immediate-fill possibility only after open-order reading succeeds at least once.
  - If open-order reading never succeeds, the order is reported as failed instead of success.
- Follow-up:
  - Added automatic Google Sheet updates for the order sheet "program trade info" area.
  - After HTS balance/open-order reads, the app writes latest update time, current tier, current price, HTS balance quantity, tier quantity gap, buy open-order count, and sell open-order count to `K6`, `K8`, `K10`, `K12`, `K14`, `K16`, and `K18`.
  - This uses the same service-account credential as settlement sheet writing. If the credential file is missing or the service account is not shared as an editor, the app logs the write failure and continues trading.

### Notes

- The working tree still contains local, uncommitted source changes and local build folders.
- Temporary build folders include `build_local/`, `dist_local/`, `build_rebalance_test/`, and `dist_rebalance_test/`.
