from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.runtime_state import StateStore


class RuntimeStateTest(unittest.TestCase):
    def test_initializes_missing_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "state.json"
            store = StateStore(path)
            state = store.load()
            self.assertFalse(state.halted)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()

