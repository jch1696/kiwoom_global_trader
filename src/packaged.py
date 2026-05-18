from __future__ import annotations

import sys


def main() -> None:
    if "--apply-update" in sys.argv:
        from src.updater import apply_update_from_argv

        raise SystemExit(apply_update_from_argv(sys.argv[1:]))

    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from src.main import main as cli_main

        cli_main()
        return

    from src.console import main as console_main

    console_main()


if __name__ == "__main__":
    main()
