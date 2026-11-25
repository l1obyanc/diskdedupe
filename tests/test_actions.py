from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from diskdedupe.actions import (
    MANIFEST_NAME,
    apply_quarantine,
    build_quarantine_plan,
    choose_keeper,
    restore_from_manifest,
)
from diskdedupe.models import DuplicateGroup, FileRecord
from diskdedupe.scanner import ScanOptions, find_duplicates
from tests.support import write_file


class KeeperStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.early = FileRecord(path=Path("/data/a.bin"), size=10, mtime=1.0)
        self.late = FileRecord(path=Path("/data/b.bin"), size=10, mtime=3.0)
        self.group = DuplicateGroup(digest="digest", size=10, files=(self.late, self.early))

    def test_first_keeps_lowest_path(self) -> None:
        self.assertEqual(choose_keeper(self.group, "first").path, self.early.path)

    def test_last_keeps_highest_path(self) -> None:
        self.assertEqual(choose_keeper(self.group, "last").path, self.late.path)

    def test_newest_keeps_latest_mtime(self) -> None:
        self.assertEqual(choose_keeper(self.group, "newest").path, self.late.path)

    def test_oldest_keeps_earliest_mtime(self) -> None:
        self.assertEqual(choose_keeper(self.group, "oldest").path, self.early.path)

    def test_unknown_strategy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            choose_keeper(self.group, "smallest")


class QuarantineTests(unittest.TestCase):
    content = b"jpeg-data" * 1024

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="diskdedupe-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "photos"
        self.copy = self.tmp / "backup"
        self.original = write_file(self.root / "2019" / "trip.jpg", self.content)
        self.duplicate = write_file(self.copy / "trip.jpg", self.content)
        self.destination = self.tmp / "quarantine"
        result = find_duplicates(ScanOptions(roots=(self.root, self.copy)))
        self.plan = build_quarantine_plan(result, self.destination)

    def test_plan_moves_only_redundant_copies(self) -> None:
        self.assertEqual(len(self.plan.moves), 1)
        move = self.plan.moves[0]
        self.assertEqual(move.original, self.original)
        self.assertEqual(move.kept, self.duplicate)
        self.assertEqual(self.plan.total_bytes, len(self.content))

    def test_plan_preserves_relative_location(self) -> None:
        expected = self.destination / "files" / "photos" / "2019" / "trip.jpg"
        self.assertEqual(self.plan.moves[0].quarantined, expected)

    def test_plan_rejects_non_empty_destination(self) -> None:
        write_file(self.destination / "existing.txt", b"data")
        result = find_duplicates(ScanOptions(roots=(self.root, self.copy)))
        with self.assertRaises(ValueError):
            build_quarantine_plan(result, self.destination)

    def test_unique_targets_for_colliding_names(self) -> None:
        write_file(self.tmp / "b" / "photos" / "x.bin", b"same")
        write_file(self.tmp / "c" / "photos" / "x.bin", b"same")
        write_file(self.tmp / "a" / "photos" / "x.bin", b"same")
        result = find_duplicates(
            ScanOptions(
                roots=(
                    self.tmp / "a" / "photos",
                    self.tmp / "b" / "photos",
                    self.tmp / "c" / "photos",
                )
            )
        )

        plan = build_quarantine_plan(result, self.tmp / "unique")
        targets = [move.quarantined for move in plan.moves]

        self.assertEqual(len(targets), 2)
        self.assertEqual(len(set(targets)), 2)

    def test_dry_run_leaves_files_in_place(self) -> None:
        apply_quarantine(self.plan, dry_run=True)

        self.assertTrue(self.original.exists())
        self.assertFalse(self.destination.exists())

    def test_apply_moves_files_and_writes_manifest(self) -> None:
        apply_quarantine(self.plan)

        move = self.plan.moves[0]
        self.assertFalse(self.original.exists())
        self.assertTrue(move.quarantined.exists())
        self.assertTrue(move.quarantined.read_bytes() == self.content)
        self.assertTrue(self.duplicate.exists())

        manifest_path = self.destination / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["total_files"], 1)
        self.assertEqual(manifest["entries"][0]["original"], str(self.original))

    def test_restore_returns_files(self) -> None:
        apply_quarantine(self.plan)

        outcome = restore_from_manifest(self.plan.manifest_path)

        self.assertEqual(outcome.restored, (self.original,))
        self.assertEqual(outcome.conflicts, ())
        self.assertTrue(self.original.exists())
        self.assertFalse(self.plan.moves[0].quarantined.exists())

    def test_restore_dry_run_moves_nothing(self) -> None:
        apply_quarantine(self.plan)

        outcome = restore_from_manifest(self.plan.manifest_path, dry_run=True)

        self.assertEqual(len(outcome.restored), 1)
        self.assertFalse(self.original.exists())
        self.assertTrue(self.plan.moves[0].quarantined.exists())

    def test_restore_skips_conflicts_unless_overwriting(self) -> None:
        apply_quarantine(self.plan)
        replacement = write_file(self.original, b"newer content")

        outcome = restore_from_manifest(self.plan.manifest_path)

        self.assertEqual(outcome.conflicts, (self.original,))
        self.assertEqual(replacement.read_bytes(), b"newer content")
        self.assertTrue(self.plan.moves[0].quarantined.exists())

        outcome = restore_from_manifest(self.plan.manifest_path, overwrite=True)

        self.assertEqual(outcome.restored, (self.original,))
        self.assertEqual(self.original.read_bytes(), self.content)

    def test_restore_reports_missing_quarantined_files(self) -> None:
        apply_quarantine(self.plan)
        self.plan.moves[0].quarantined.unlink()

        outcome = restore_from_manifest(self.plan.manifest_path)

        self.assertEqual(outcome.missing, (self.original,))

    def test_restore_rejects_unknown_manifest_version(self) -> None:
        manifest_path = write_file(
            self.tmp / MANIFEST_NAME,
            json.dumps({"version": 99, "entries": []}).encode("utf-8"),
        )

        with self.assertRaises(ValueError):
            restore_from_manifest(manifest_path)

    def test_restore_rejects_paths_outside_quarantine(self) -> None:
        manifest_path = write_file(
            self.tmp / MANIFEST_NAME,
            json.dumps(
                {
                    "version": 1,
                    "entries": [{"quarantined": "../escape.bin", "original": str(self.tmp / "x.bin")}],
                }
            ).encode("utf-8"),
        )

        with self.assertRaises(ValueError):
            restore_from_manifest(manifest_path)

    def test_apply_without_duplicates_creates_nothing(self) -> None:
        result = find_duplicates(ScanOptions(roots=(self.root,)))
        destination = self.tmp / "empty-quarantine"
        plan = build_quarantine_plan(result, destination)

        apply_quarantine(plan)

        self.assertEqual(plan.moves, ())
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
