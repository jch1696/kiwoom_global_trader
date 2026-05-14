from __future__ import annotations

import unittest
import tempfile
import zipfile
from pathlib import Path

from src.config import GoogleConfig
from src.sheet_reader import LocalWorkbookSheetReader, PublicCsvSheetReader, PublicXlsxSheetReader


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

    def test_reads_public_xlsx_export_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.xlsx"
            _write_minimal_strategy_xlsx(path)

            reader = PublicXlsxSheetReader(GoogleConfig(public_xlsx_url=path.as_uri()))
            strategies = reader.read_strategies()

        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0].sheet_name, "TQQQ50")
        self.assertEqual(strategies[0].symbol, "TQQQ")


def _write_minimal_strategy_xlsx(path: Path) -> None:
    shared = ["12345678", "TQQQ", "TRUE"]
    sheet_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="5"><c r="V5"><v>0</v></c><c r="W5"><v>0</v></c><c r="X5"><v>0</v></c><c r="Y5"><v>0</v></c><c r="Z5"><v>10</v></c><c r="AA5"><v>1</v></c><c r="AB5"><v>0</v></c><c r="AC5"><v>0</v></c></row>
    <row r="6"><c r="E6" t="s"><v>0</v></c><c r="V6"><v>1</v></c><c r="W6"><v>10</v></c><c r="X6"><v>100</v></c><c r="Y6"><v>10</v></c><c r="Z6"><v>9</v></c><c r="AA6"><v>1</v></c><c r="AB6"><v>11</v></c><c r="AC6"><v>1</v></c></row>
    <row r="8"><c r="E8" t="s"><v>1</v></c><c r="V8"><v>3</v></c><c r="W8"><v>30</v></c><c r="X8"><v>100</v></c><c r="Y8"><v>8</v></c><c r="Z8"><v>0</v></c><c r="AA8"><v>0</v></c><c r="AB8"><v>9</v></c><c r="AC8"><v>1</v></c></row>
    <row r="10"><c r="E10"><v>1000</v></c></row>
    <row r="12"><c r="E12"><v>3</v></c></row>
    <row r="14"><c r="E14"><v>10</v></c></row>
    <row r="16"><c r="E16" t="s"><v>2</v></c></row>
  </sheetData>
</worksheet>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="TQQQ50" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr(
            "xl/sharedStrings.xml",
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"3\" uniqueCount=\"3\">"
            + "".join(f"<si><t>{value}</t></si>" for value in shared)
            + "</sst>",
        )


if __name__ == "__main__":
    unittest.main()
