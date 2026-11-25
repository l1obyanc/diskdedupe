"""Command-line interface for diskdedupe."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Sequence

from diskdedupe import __version__
from diskdedupe.actions import (
    KEEP_STRATEGIES,
    QuarantinePlan,
    apply_quarantine,
    build_quarantine_plan,
    restore_from_manifest,
)
from diskdedupe.formatting import (
    Palette,
    parse_size,
    render_quarantine_plan,
    render_restore_result,
    render_scan_report,
)
from diskdedupe.models import ScanResult
from diskdedupe.scanner import ScanOptions, find_duplicates

ErrorPrinter = Callable[[OSError], None]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diskdedupe",
        description="Find duplicate files by content and quarantine redundant copies without deleting data.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    scan = subparsers.add_parser("scan", help="report duplicate files")
    _add_scan_options(scan)
    scan.add_argument("--json", action="store_true", help="print the report as JSON")
    scan.set_defaults(handler=_run_scan)

    quarantine = subparsers.add_parser(
        "quarantine", help="move redundant copies into a quarantine folder"
    )
    _add_scan_options(quarantine)
    quarantine.add_argument(
        "--into",
        required=True,
        type=Path,
        metavar="DIR",
        help="quarantine folder to create; it must be absent or empty",
    )
    quarantine.add_argument(
        "--keep",
        choices=KEEP_STRATEGIES,
        default="first",
        help="copy to keep: first or last in sorted path order, or newest/oldest by modification time (default: first)",
    )
    quarantine.add_argument(
        "--apply", action="store_true", help="actually move files; without this flag only the plan is printed"
    )
    quarantine.add_argument("--json", action="store_true", help="print the plan as JSON")
    quarantine.set_defaults(handler=_run_quarantine)

    restore = subparsers.add_parser(
        "restore", help="move quarantined files back to their original paths"
    )
    restore.add_argument("manifest", type=Path, metavar="MANIFEST", help="manifest.json written by quarantine")
    restore.add_argument(
        "--apply", action="store_true", help="actually move files; without this flag only the plan is printed"
    )
    restore.add_argument(
        "--overwrite", action="store_true", help="replace files that already exist at the original path"
    )
    restore.add_argument("--no-color", action="store_true", help="disable colored output")
    restore.set_defaults(handler=_run_restore)

    return parser


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="files or folders to scan")
    parser.add_argument(
        "--min-size",
        type=_size_argument,
        default=1,
        metavar="SIZE",
        help="skip files smaller than SIZE, e.g. 0, 64k, 10MB (default: 1, which skips empty files)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip paths matching a glob, relative to each scanned root or by file name; repeatable",
    )
    parser.add_argument("--no-color", action="store_true", help="disable colored output")


def _size_argument(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _palette(args: argparse.Namespace) -> Palette:
    enabled = not args.no_color and "NO_COLOR" not in os.environ and _stdout_supports_color()
    return Palette(enabled=enabled)


def _stdout_supports_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    return _enable_windows_ansi()


def _enable_windows_ansi() -> bool:
    """Ask the legacy Windows console to interpret ANSI escape sequences."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False


def _error_printer() -> ErrorPrinter:
    def report(exc: OSError) -> None:
        print(f"warning: {exc}", file=sys.stderr)

    return report


def _scan_options(args: argparse.Namespace) -> ScanOptions:
    return ScanOptions(
        roots=tuple(args.paths),
        exclude=tuple(args.exclude),
        min_size=args.min_size,
    )


def _run_scan(args: argparse.Namespace) -> int:
    result = find_duplicates(_scan_options(args), _error_printer())
    if args.json:
        print(json.dumps(_scan_payload(result), indent=2))
    else:
        print(render_scan_report(result, _palette(args)))
    return 1 if result.groups else 0


def _run_quarantine(args: argparse.Namespace) -> int:
    result = find_duplicates(_scan_options(args), _error_printer())
    plan = build_quarantine_plan(result, args.into, strategy=args.keep)
    dry_run = not args.apply

    apply_quarantine(plan, dry_run=dry_run)

    if args.json:
        print(json.dumps(_plan_payload(plan, dry_run=dry_run), indent=2))
    else:
        print(render_quarantine_plan(plan, _palette(args), dry_run=dry_run))
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    outcome = restore_from_manifest(args.manifest, dry_run=not args.apply, overwrite=args.overwrite)
    print(render_restore_result(outcome, _palette(args), dry_run=not args.apply))
    return 1 if outcome.conflicts or outcome.missing else 0


def _scan_payload(result: ScanResult) -> dict[str, object]:
    return {
        "roots": [str(root) for root in result.roots],
        "files_scanned": result.files_scanned,
        "summary": {
            "duplicate_groups": len(result.groups),
            "redundant_files": result.redundant_files,
            "reclaimable_bytes": result.reclaimable_bytes,
        },
        "groups": [
            {
                "digest": group.digest,
                "size": group.size,
                "wasted_bytes": group.wasted_bytes,
                "files": [str(record.path) for record in group.files],
            }
            for group in result.groups
        ],
    }


def _plan_payload(plan: QuarantinePlan, dry_run: bool) -> dict[str, object]:
    return {
        "destination": str(plan.destination),
        "dry_run": dry_run,
        "total_files": len(plan.moves),
        "total_bytes": plan.total_bytes,
        "moves": [
            {
                "original": str(move.original),
                "quarantined": str(move.quarantined),
                "kept": str(move.kept),
                "size": move.size,
                "digest": move.digest,
            }
            for move in plan.moves
        ],
    }
