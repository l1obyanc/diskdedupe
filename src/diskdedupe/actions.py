"""Quarantine and restore operations.

Quarantine never deletes data: redundant copies are moved into a destination
folder and recorded in ``manifest.json`` so the operation can be undone.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from diskdedupe.models import DuplicateGroup, FileRecord, ScanResult, path_order

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
KEEP_STRATEGIES = ("first", "last", "newest", "oldest")


@dataclass(frozen=True, slots=True)
class QuarantineMove:
    """One redundant file scheduled to move into quarantine."""

    original: Path
    quarantined: Path
    kept: Path
    digest: str
    size: int


@dataclass(frozen=True, slots=True)
class QuarantinePlan:
    """Set of moves produced by :func:`build_quarantine_plan`."""

    destination: Path
    moves: tuple[QuarantineMove, ...]

    @property
    def total_bytes(self) -> int:
        return sum(move.size for move in self.moves)

    @property
    def manifest_path(self) -> Path:
        return self.destination / MANIFEST_NAME


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Outcome of :func:`restore_from_manifest`."""

    restored: tuple[Path, ...]
    conflicts: tuple[Path, ...]
    missing: tuple[Path, ...]


def choose_keeper(group: DuplicateGroup, strategy: str = "first") -> FileRecord:
    """Pick the copy to keep according to ``strategy``."""
    ordered = sorted(group.files, key=path_order)
    if strategy == "first":
        return ordered[0]
    if strategy == "last":
        return ordered[-1]
    if strategy == "newest":
        return max(ordered, key=lambda record: (record.mtime, path_order(record)))
    if strategy == "oldest":
        return min(ordered, key=lambda record: (record.mtime, path_order(record)))
    raise ValueError(f"unknown keep strategy: {strategy}")


def build_quarantine_plan(
    result: ScanResult, destination: Path, strategy: str = "first"
) -> QuarantinePlan:
    """Return the moves that would quarantine every redundant copy."""
    if strategy not in KEEP_STRATEGIES:
        raise ValueError(f"unknown keep strategy: {strategy}")

    destination = Path(destination).absolute()
    _ensure_usable_destination(destination)

    roots = tuple(Path(root).absolute() for root in result.roots)
    reserved: set[str] = set()
    moves: list[QuarantineMove] = []

    for group in result.groups:
        keeper = choose_keeper(group, strategy)
        for record in sorted(group.files, key=path_order):
            if record.path == keeper.path:
                continue
            folder, relative = _relative_location(record.path, roots)
            target = _unique_target(destination / "files" / folder / relative, reserved)
            moves.append(
                QuarantineMove(
                    original=record.path,
                    quarantined=target,
                    kept=keeper.path,
                    digest=group.digest,
                    size=group.size,
                )
            )

    return QuarantinePlan(destination=destination, moves=tuple(moves))


def apply_quarantine(plan: QuarantinePlan, dry_run: bool = False) -> None:
    """Execute a plan and write its manifest, unless ``dry_run`` is true."""
    if dry_run or not plan.moves:
        return

    plan.destination.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    failures: list[OSError] = []
    moved_bytes = 0

    for move in plan.moves:
        try:
            move.quarantined.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move.original), str(move.quarantined))
        except OSError as exc:
            failures.append(exc)
            continue
        moved_bytes += move.size
        entries.append(
            {
                "quarantined": move.quarantined.relative_to(plan.destination).as_posix(),
                "original": str(move.original),
                "kept": str(move.kept),
                "size": move.size,
                "digest": move.digest,
            }
        )

    manifest = {
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "destination": str(plan.destination),
        "total_files": len(entries),
        "total_bytes": moved_bytes,
        "entries": entries,
    }
    plan.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if failures:
        raise OSError(
            f"{len(failures)} of {len(plan.moves)} files could not be moved; a manifest "
            f"for the {len(entries)} completed moves was written to {plan.manifest_path} "
            f"(first error: {failures[0]})"
        )


def restore_from_manifest(
    manifest_path: Path, dry_run: bool = False, overwrite: bool = False
) -> RestoreResult:
    """Move quarantined files back to their recorded original paths."""
    manifest_path = Path(manifest_path).absolute()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = _validated_entries(data, manifest_path)
    base = manifest_path.parent

    restored: list[Path] = []
    conflicts: list[Path] = []
    missing: list[Path] = []

    for entry in entries:
        quarantined = base / str(entry["quarantined"])
        original = Path(str(entry["original"]))

        if not quarantined.is_file():
            missing.append(original)
            continue
        if original.exists() and not overwrite:
            conflicts.append(original)
            continue
        if original.is_dir():
            raise ValueError(f"cannot restore a file over a directory: {original}")

        restored.append(original)
        if dry_run:
            continue

        original.parent.mkdir(parents=True, exist_ok=True)
        if original.exists():
            original.unlink()
        shutil.move(str(quarantined), str(original))

    return RestoreResult(tuple(restored), tuple(conflicts), tuple(missing))


def _ensure_usable_destination(destination: Path) -> None:
    if not destination.exists():
        return
    if not destination.is_dir():
        raise ValueError(f"quarantine destination is not a directory: {destination}")
    if any(destination.iterdir()):
        raise ValueError(f"quarantine destination is not empty: {destination}")


def _relative_location(path: Path, roots: tuple[Path, ...]) -> tuple[str, Path]:
    """Return a folder name and path relative to the scanned root that owns ``path``."""
    for root in roots:
        base = root.parent if root.is_file() else root
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        return base.name or "root", relative
    return "loose", Path(path.name)


def _unique_target(candidate: Path, reserved: set[str]) -> Path:
    key = os.path.normcase(str(candidate))
    if key not in reserved and not candidate.exists():
        reserved.add(key)
        return candidate

    index = 1
    while True:
        alternative = candidate.with_name(f"{candidate.stem} ({index}){candidate.suffix}")
        key = os.path.normcase(str(alternative))
        if key not in reserved and not alternative.exists():
            reserved.add(key)
            return alternative
        index += 1


def _validated_entries(data: object, manifest_path: Path) -> list[dict[str, object]]:
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest format: {manifest_path}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"manifest has no entries list: {manifest_path}")

    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry.get(key) for key in ("quarantined", "original")
        ):
            raise ValueError(f"manifest entry is malformed: {manifest_path}")
        quarantined = Path(entry["quarantined"])
        if quarantined.is_absolute() or ".." in quarantined.parts:
            raise ValueError(
                f"manifest entry points outside the quarantine folder: {entry['quarantined']}"
            )
        if not Path(entry["original"]).is_absolute():
            raise ValueError(f"manifest entry has a relative original path: {entry['original']}")
    return entries
