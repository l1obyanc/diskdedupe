from __future__ import annotations

import unittest

from diskdedupe.formatting import Palette, human_bytes, parse_size, render_scan_report
from diskdedupe.models import ScanResult


class HumanBytesTests(unittest.TestCase):
    def test_values(self) -> None:
        cases = {
            0: "0 B",
            1023: "1023 B",
            1024: "1.0 KB",
            1536: "1.5 KB",
            10 * 1024: "10 KB",
            1024**2: "1.0 MB",
            1024**3: "1.0 GB",
            1024**4: "1.0 TB",
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(human_bytes(size), expected)


class ParseSizeTests(unittest.TestCase):
    def test_accepts_units(self) -> None:
        cases = {
            "0": 0,
            "512": 512,
            "64k": 64 * 1024,
            "10MB": 10 * 1024**2,
            "1.5M": 1572864,
            "2 GB": 2 * 1024**3,
            "1TB": 1024**4,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_size(text), expected)

    def test_rejects_invalid_input(self) -> None:
        for text in ("", "abc", "-5", "10x"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_size(text)


class RenderTests(unittest.TestCase):
    def test_palette_can_be_disabled(self) -> None:
        palette = Palette(enabled=False)

        self.assertEqual(palette.paint("text", "bold"), "text")

    def test_palette_adds_ansi_codes_when_enabled(self) -> None:
        palette = Palette(enabled=True)

        self.assertEqual(palette.paint("text", "green"), "\x1b[32mtext\x1b[0m")

    def test_scan_report_without_duplicates(self) -> None:
        result = ScanResult(roots=(), groups=(), files_scanned=3)

        report = render_scan_report(result, Palette())

        self.assertIn("Scanned 3 files", report)
        self.assertIn("No duplicate files found.", report)


if __name__ == "__main__":
    unittest.main()
