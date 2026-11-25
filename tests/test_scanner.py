from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from diskdedupe.models import ScanResult
from diskdedupe.scanner import PARTIAL_READ_BYTES, ScanOptions, find_duplicates, hash_file
from tests.support import write_file


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="diskdedupe-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def scan(self, *paths: Path, exclude: tuple[str, ...] = (), min_size: int = 1) -> ScanResult:
        options = ScanOptions(
            roots=tuple(paths), exclude=exclude, min_size=min_size
        )
        return find_duplicates(options)

    def test_groups_identical_content(self) -> None:
        content = os.urandom(8192)
        write_file(self.tmp / "a" / "one.bin", content)
        write_file(self.tmp / "b" / "two.bin", content)
        write_file(self.tmp / "c" / "other.bin", os.urandom(8192))

        result = self.scan(self.tmp)

        self.assertEqual(len(result.groups), 1)
        self.assertEqual(len(result.groups[0].files), 2)
        self.assertEqual(result.reclaimable_bytes, 8192)
        self.assertEqual(result.files_scanned, 3)

    def test_same_size_different_content_is_not_duplicate(self) -> None:
        write_file(self.tmp / "a.bin", b"a" * 4096)
        write_file(self.tmp / "b.bin", b"b" * 4096)

        self.assertEqual(self.scan(self.tmp).groups, ())

    def test_partial_hash_requires_full_match(self) -> None:
        prefix = b"x" * PARTIAL_READ_BYTES
        write_file(self.tmp / "a.bin", prefix + b"1" * 16)
        write_file(self.tmp / "b.bin", prefix + b"2" * 16)

        self.assertEqual(self.scan(self.tmp).groups, ())

    def test_empty_files_skipped_by_default(self) -> None:
        write_file(self.tmp / "a.txt", b"")
        write_file(self.tmp / "b.txt", b"")

        self.assertEqual(self.scan(self.tmp).groups, ())
        self.assertEqual(len(self.scan(self.tmp, min_size=0).groups), 1)

    def test_min_size_filter(self) -> None:
        write_file(self.tmp / "a.bin", b"z" * 100)
        write_file(self.tmp / "b.bin", b"z" * 100)

        self.assertEqual(self.scan(self.tmp, min_size=101).groups, ())
        self.assertEqual(len(self.scan(self.tmp, min_size=100).groups), 1)

    def test_exclude_glob(self) -> None:
        write_file(self.tmp / "keep.bin", b"z" * 2048)
        write_file(self.tmp / "skip.bak", b"z" * 2048)

        self.assertEqual(self.scan(self.tmp, exclude=("*.bak",)).groups, ())

    def test_nested_roots_do_not_report_self_duplicates(self) -> None:
        write_file(self.tmp / "a.bin", b"q" * 100)
        write_file(self.tmp / "sub" / "b.bin", b"q" * 100)

        result = self.scan(self.tmp, self.tmp / "sub")

        self.assertEqual(len(result.groups), 1)
        self.assertEqual(len(result.groups[0].files), 2)

    def test_hardlinks_count_as_single_file(self) -> None:
        source = write_file(self.tmp / "a.bin", b"h" * 1024)
        try:
            os.link(source, self.tmp / "b.bin")
        except OSError:
            self.skipTest("hard links are not supported on this filesystem")

        self.assertEqual(self.scan(self.tmp).groups, ())

    def test_symlinks_are_ignored(self) -> None:
        target = write_file(self.tmp / "a.bin", b"s" * 512)
        try:
            (self.tmp / "link.bin").symlink_to(target)
        except OSError:
            self.skipTest("creating symlinks requires elevated privileges")

        self.assertEqual(self.scan(self.tmp).groups, ())

    def test_missing_root_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.scan(self.tmp / "nope")

    def test_groups_sorted_by_wasted_bytes(self) -> None:
        small = b"s" * 100
        write_file(self.tmp / "s1.bin", small)
        write_file(self.tmp / "s2.bin", small)
        big = b"b" * 5000
        for name in ("b1.bin", "b2.bin", "b3.bin"):
            write_file(self.tmp / name, big)

        sizes = [group.size for group in self.scan(self.tmp).groups]

        self.assertEqual(sizes, [5000, 100])

    def test_can_scan_explicit_file_paths(self) -> None:
        first = write_file(self.tmp / "a.bin", b"x" * 10)
        second = write_file(self.tmp / "b.bin", b"x" * 10)

        self.assertEqual(len(self.scan(first, second).groups), 1)

    def test_hash_file_limit(self) -> None:
        path = write_file(self.tmp / "a.bin", b"0123456789")

        expected = hashlib.blake2b(b"0123", digest_size=32).hexdigest()

        self.assertEqual(hash_file(path, limit=4), expected)
        self.assertEqual(hash_file(path), hashlib.blake2b(b"0123456789", digest_size=32).hexdigest())


if __name__ == "__main__":
    unittest.main()
