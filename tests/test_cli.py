from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from diskdedupe.cli import main
from tests.support import write_file


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="diskdedupe-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.content = b"duplicate payload" * 512
        self.first = write_file(self.tmp / "photos" / "a.jpg", self.content)
        self.second = write_file(self.tmp / "backup" / "a.jpg", self.content)
        self.destination = self.tmp / "quarantine"

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(list(argv))
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_scan_finds_duplicates_and_exits_with_one(self) -> None:
        code, out, _ = self.run_cli("scan", str(self.tmp), "--no-color")

        self.assertEqual(code, 1)
        self.assertIn("duplicate group", out)
        self.assertIn(str(self.first), out)

    def test_scan_returns_zero_when_clean(self) -> None:
        self.second.unlink()
        write_file(self.tmp / "photos" / "unique.jpg", b"unique")

        code, out, _ = self.run_cli("scan", str(self.tmp), "--no-color")

        self.assertEqual(code, 0)
        self.assertIn("No duplicate files found.", out)

    def test_scan_json_report(self) -> None:
        code, out, _ = self.run_cli("scan", str(self.tmp), "--json")

        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["summary"]["duplicate_groups"], 1)
        self.assertEqual(payload["summary"]["redundant_files"], 1)
        self.assertEqual(payload["groups"][0]["size"], len(self.content))

    def test_missing_path_exits_with_two(self) -> None:
        code, _, err = self.run_cli("scan", str(self.tmp / "missing"))

        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_invalid_size_exits_with_two(self) -> None:
        code, _, err = self.run_cli("scan", str(self.tmp), "--min-size", "huge")

        self.assertEqual(code, 2)
        self.assertIn("invalid size", err)

    def test_quarantine_dry_run_by_default(self) -> None:
        code, out, _ = self.run_cli("quarantine", str(self.tmp), "--into", str(self.destination))

        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)
        self.assertTrue(self.first.exists())
        self.assertTrue(self.second.exists())
        self.assertFalse(self.destination.exists())

    def test_quarantine_apply_moves_files(self) -> None:
        code, out, _ = self.run_cli(
            "quarantine", str(self.tmp), "--into", str(self.destination), "--apply"
        )

        self.assertEqual(code, 0)
        self.assertIn("Done.", out)
        self.assertEqual(sum(path.exists() for path in (self.first, self.second)), 1)
        manifest = json.loads((self.destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["total_files"], 1)

    def test_quarantine_rejects_non_empty_destination(self) -> None:
        write_file(self.destination / "keep.txt", b"data")

        code, _, err = self.run_cli(
            "quarantine", str(self.tmp), "--into", str(self.destination), "--apply"
        )

        self.assertEqual(code, 2)
        self.assertIn("not empty", err)

    def test_quarantine_json_plan(self) -> None:
        code, out, _ = self.run_cli(
            "quarantine", str(self.tmp), "--into", str(self.destination), "--json"
        )

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["total_files"], 1)
        self.assertEqual(len(payload["moves"]), 1)

    def test_restore_brings_quarantined_files_back(self) -> None:
        self.run_cli("quarantine", str(self.tmp), "--into", str(self.destination), "--apply")
        manifest = self.destination / "manifest.json"

        code, out, _ = self.run_cli("restore", str(manifest), "--apply")

        self.assertEqual(code, 0)
        self.assertIn("Restored 1 file", out)
        self.assertTrue(self.first.exists())
        self.assertTrue(self.second.exists())

    def test_version_flag(self) -> None:
        code, out, _ = self.run_cli("--version")

        self.assertEqual(code, 0)
        self.assertIn("diskdedupe", out)


if __name__ == "__main__":
    unittest.main()
