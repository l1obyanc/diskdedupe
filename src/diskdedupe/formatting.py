"""Human-readable output helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from diskdedupe.actions import QuarantinePlan, RestoreResult
from diskdedupe.models import ScanResult

_STYLES = {
    "bold": "1",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
}
_RESET = "\x1b[0m"

_UNITS = ("KB", "MB", "GB", "TB", "PB")
_SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*([kmgtp]?b?)", re.IGNORECASE)
_SIZE_FACTORS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "p": 1024**5,
    "pb": 1024**5,
}


@dataclass(frozen=True, slots=True)
class Palette:
    """Optional ANSI styling; a disabled palette returns text unchanged."""

    enabled: bool = False

    def paint(self, text: str, style: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[{_STYLES[style]}m{text}{_RESET}"


def human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"

    value = float(size)
    unit = "B"
    for unit in _UNITS:
        value /= 1024
        if value < 1024:
            break
    precision = 1 if value < 10 else 0
    return f"{value:.{precision}f} {unit}"


def parse_size(text: str) -> int:
    """Parse sizes such as ``0``, ``64k`` or ``10MB`` into bytes."""
    match = _SIZE_PATTERN.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"invalid size {text!r}; expected a number with an optional unit, e.g. 0, 64k, 10MB")
    amount, suffix = match.groups()
    return int(float(amount) * _SIZE_FACTORS[suffix.lower()])


def render_scan_report(result: ScanResult, palette: Palette) -> str:
    roots = ", ".join(str(root) for root in result.roots)
    lines = [palette.paint(f"Scanned {result.files_scanned} files under: {roots}", "bold")]

    if not result.groups:
        lines.append(palette.paint("No duplicate files found.", "green"))
        return "\n".join(lines)

    group_count = len(result.groups)
    redundant = result.redundant_files
    lines.append(
        f"Found {group_count} duplicate {_plural(group_count, 'group')}: "
        f"{redundant} redundant {_plural(redundant, 'file')}, "
        f"{human_bytes(result.reclaimable_bytes)} reclaimable."
    )

    for index, group in enumerate(result.groups, start=1):
        copies = len(group.files)
        header = (
            f"[{index}] {copies} copies, {human_bytes(group.size)} each, "
            f"{human_bytes(group.wasted_bytes)} wasted"
        )
        lines.append("")
        lines.append(palette.paint(header, "cyan"))
        lines.extend(f"    {record.path}" for record in group.files)

    return "\n".join(lines)


def render_quarantine_plan(plan: QuarantinePlan, palette: Palette, dry_run: bool) -> str:
    if not plan.moves:
        return palette.paint("No duplicate files found; nothing to quarantine.", "green")

    count = len(plan.moves)
    lines = [
        palette.paint(
            f"Quarantine plan: move {count} {_plural(count, 'file')} "
            f"({human_bytes(plan.total_bytes)}) into {plan.destination}",
            "bold",
        )
    ]

    for index, move in enumerate(plan.moves, start=1):
        lines.append("")
        lines.append(palette.paint(f"[{index}] keep {move.kept}", "green"))
        lines.append(f"    move {move.original} -> {move.quarantined}")

    lines.append("")
    if dry_run:
        lines.append(palette.paint("Dry run: nothing was moved. Re-run with --apply to execute this plan.", "yellow"))
    else:
        lines.append(
            palette.paint(f'Done. Undo with: diskdedupe restore "{plan.manifest_path}"', "green")
        )

    return "\n".join(lines)


def render_restore_result(result: RestoreResult, palette: Palette, dry_run: bool) -> str:
    action = "Would restore" if dry_run else "Restored"
    restored = len(result.restored)
    lines = [palette.paint(f"{action} {restored} {_plural(restored, 'file')}.", "bold")]
    lines.extend(f"    {path}" for path in result.restored)

    if result.conflicts:
        count = len(result.conflicts)
        lines.append(
            palette.paint(
                f"Skipped {count} {_plural(count, 'file')} because the original path exists "
                "(use --overwrite to replace):",
                "yellow",
            )
        )
        lines.extend(f"    {path}" for path in result.conflicts)

    if result.missing:
        count = len(result.missing)
        lines.append(palette.paint(f"Missing {count} quarantined {_plural(count, 'file')}:", "red"))
        lines.extend(f"    {path}" for path in result.missing)

    if dry_run and result.restored:
        lines.append(palette.paint("Dry run: nothing was moved. Re-run with --apply to restore.", "yellow"))

    return "\n".join(lines)


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
