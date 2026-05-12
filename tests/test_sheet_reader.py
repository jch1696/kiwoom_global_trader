from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.config import GoogleConfig
from src.sheet_reader import LocalWorkbookSheetReader, PublicCsvSheetReader


class SheetReaderTest(unittest.TestCase):
    def test_reads_existing_program_workbook(self) -> None:
        root = Path(__file__).resolve().parents[2]
        workbook = root / "LINED" / "program.xlsx"
        if not workbook.exists():
            self.skipTest(f"local workbook fixture not found: {workbook}")
        reader = LocalWorkbookSheetReader(
            GoogleConfig(local_workbook_path="LINED/program.xlsx"),
            root,
        )
        strategies = reader.read_strategies()
        names = {strategy.sheet_name for strategy in strategies}
        self.assertIn("LABU", names)
        self.assertIn("SOXL", names)
        labu = next(strategy for strategy in strategies if strategy.sheet_name == "LABU")
        self.assertEqual(labu.symbol, "LABU")
        self.assertGreater(len(labu.tiers), 0)

    def test_reads_public_csv_tab_shape(self) -> None:
        rows = [["" for _ in range(29)] for _ in range(20)]
        rows[5][4] = "12345678"  # E6
        rows[7][4] = "SOXL"      # E8
        rows[9][4] = "1000"     # E10
        rows[11][4] = "3"       # E12
        rows[13][4] = "10"      # E14
        rows[15][4] = "TRUE"    # E16
        rows[4][21:29] = ["0", "0", "0", "0", "10", "1", "0", "0"]
        rows[5][21:29] = ["1", "10", "100", "10", "9", "1", "11", "1"]
        rows[6][21:29] = ["2", "20", "100", "9", "8", "1", "10", "1"]
        rows[7][21:29] = ["3", "30", "100", "8", "0", "0", "9", "1"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.csv"
            path.write_text("\n".join(",".join(row) for row in rows), encoding="utf-8")
            reader = PublicCsvSheetReader(GoogleConfig(public_csv_tabs={"SOXL": path.as_uri()}))
            strategies = reader.read_strategies()
        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0].symbol, "SOXL")


if __name__ == "__main__":
    unittest.main()
