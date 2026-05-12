from __future__ import annotations

import sys


def main() -> None:
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from src.main import main as cli_main

        cli_main()
        return

    from src.console import main as console_main

    console_main()


if __name__ == "__main__":
    main()
