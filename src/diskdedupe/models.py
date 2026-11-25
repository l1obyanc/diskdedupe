"""Data structures shared by the scanner, actions and report renderers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileRecord:
    """A regular file discovered during a scan."""

    path: Path
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Files that share identical content."""

    digest: str
    size: int
    files: tuple[FileRecord, ...]

    @property
    def wasted_bytes(self) -> int:
        return self.size * (len(self.files) - 1)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of a duplicate scan."""

    roots: tuple[Path, ...]
    groups: tuple[DuplicateGroup, ...]
    files_scanned: int

    @property
    def redundant_files(self) -> int:
        return sum(len(group.files) - 1 for group in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(group.wasted_bytes for group in self.groups)


def path_order(record: FileRecord) -> str:
    """Deterministic, platform-aware sort key for file paths."""
    return os.path.normcase(str(record.path))
