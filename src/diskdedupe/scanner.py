"""Filesystem traversal and content-based duplicate detection.

The scanner uses a three-stage pipeline so that cheap checks run first:

1. Group files by exact size; a file with a unique size cannot be a duplicate.
2. Hash the first ``PARTIAL_READ_BYTES`` of each remaining file (or the whole
   file when it is small enough).
3. Hash the full content of the few files that still collide.

Hard links are reported once because they share storage, and symlinks are
never followed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Sequence

from diskdedupe.models import DuplicateGroup, FileRecord, ScanResult, path_order

PARTIAL_READ_BYTES = 64 * 1024
READ_CHUNK_BYTES = 1024 * 1024
DIGEST_SIZE = 32

ErrorHandler = Callable[[OSError], None]


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """Configuration for :func:`find_duplicates`."""

    roots: tuple[Path, ...]
    exclude: tuple[str, ...] = ()
    min_size: int = 1


def hash_file(path: Path, limit: int | None = None) -> str:
    """Return a BLAKE2b hex digest of a file, optionally only its first bytes."""
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    remaining = limit
    with path.open("rb") as handle:
        while True:
            read_size = READ_CHUNK_BYTES if remaining is None else min(READ_CHUNK_BYTES, remaining)
            if read_size <= 0:
                break
            chunk = handle.read(read_size)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def find_duplicates(options: ScanOptions, on_error: ErrorHandler | None = None) -> ScanResult:
    """Scan ``options.roots`` and return groups of files with identical content."""
    options = replace(options, roots=tuple(Path(root).absolute() for root in options.roots))
    files = collect_files(options, on_error)

    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for record in files:
        by_size[record.size].append(record)

    partial_buckets: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    for size, records in by_size.items():
        if len(records) < 2:
            continue
        limit = PARTIAL_READ_BYTES if size > PARTIAL_READ_BYTES else None
        for record in records:
            try:
                digest = hash_file(record.path, limit=limit)
            except OSError as exc:
                _report(on_error, exc)
                continue
            partial_buckets[(size, digest)].append(record)

    groups: list[DuplicateGroup] = []
    for (size, digest), candidates in partial_buckets.items():
        if len(candidates) < 2:
            continue
        if size <= PARTIAL_READ_BYTES:
            groups.append(_build_group(digest, size, candidates))
            continue
        full_buckets: dict[str, list[FileRecord]] = defaultdict(list)
        for record in candidates:
            try:
                full_digest = hash_file(record.path)
            except OSError as exc:
                _report(on_error, exc)
                continue
            full_buckets[full_digest].append(record)
        for full_digest, matches in full_buckets.items():
            if len(matches) > 1:
                groups.append(_build_group(full_digest, size, matches))

    groups.sort(key=lambda group: (group.wasted_bytes, group.size), reverse=True)
    return ScanResult(roots=options.roots, groups=tuple(groups), files_scanned=len(files))


def collect_files(options: ScanOptions, on_error: ErrorHandler | None = None) -> list[FileRecord]:
    """Return every eligible regular file under ``options.roots``.

    Duplicate paths (from overlapping roots) and extra hard links are included
    only once.
    """
    records: list[FileRecord] = []
    seen_paths: set[str] = set()
    seen_inodes: set[tuple[int, int]] = set()

    for root in options.roots:
        root = Path(root).absolute()
        if not root.exists():
            raise FileNotFoundError(f"path does not exist: {root}")

        if root.is_file():
            if not _is_excluded(root, root.parent, options.exclude):
                try:
                    stat = root.stat()
                except OSError as exc:
                    _report(on_error, exc)
                    continue
                _register(records, root, stat, options, seen_paths, seen_inodes)
            continue

        if not root.is_dir():
            raise ValueError(f"not a regular file or directory: {root}")

        for path, stat in _iter_files(root, options, on_error):
            _register(records, path, stat, options, seen_paths, seen_inodes)

    return records


def _iter_files(
    root: Path, options: ScanOptions, on_error: ErrorHandler | None
) -> Iterator[tuple[Path, os.stat_result]]:
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if not _is_excluded(path, root, options.exclude):
                            pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if _is_excluded(path, root, options.exclude):
                        continue
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        if stat.st_ino == 0:
                            # Windows directory entries omit inode data, which is
                            # required to recognise hard links.
                            stat = os.stat(entry.path)
                        yield path, stat
                    except OSError as exc:
                        _report(on_error, exc)
        except OSError as exc:
            _report(on_error, exc)


def _register(
    records: list[FileRecord],
    path: Path,
    stat: os.stat_result,
    options: ScanOptions,
    seen_paths: set[str],
    seen_inodes: set[tuple[int, int]],
) -> None:
    if stat.st_size < options.min_size:
        return

    path_key = os.path.normcase(str(path))
    if path_key in seen_paths:
        return

    # st_ino is 0 on filesystems that cannot report it, such as some FAT mounts.
    inode = (stat.st_dev, stat.st_ino)
    if stat.st_ino and inode in seen_inodes:
        return

    seen_paths.add(path_key)
    if stat.st_ino:
        seen_inodes.add(inode)
    records.append(FileRecord(path=path, size=stat.st_size, mtime=stat.st_mtime))


def _is_excluded(path: Path, base: Path, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        relative = path.name
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def _build_group(digest: str, size: int, records: list[FileRecord]) -> DuplicateGroup:
    ordered = tuple(sorted(records, key=path_order))
    return DuplicateGroup(digest=digest, size=size, files=ordered)


def _report(on_error: ErrorHandler | None, exc: OSError) -> None:
    if on_error is not None:
        on_error(exc)
