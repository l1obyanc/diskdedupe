"""Find duplicate files by content and quarantine redundant copies safely."""

from diskdedupe.models import DuplicateGroup, FileRecord, ScanResult
from diskdedupe.scanner import ScanOptions, find_duplicates

__version__ = "0.1.0"

__all__ = [
    "DuplicateGroup",
    "FileRecord",
    "ScanOptions",
    "ScanResult",
    "__version__",
    "find_duplicates",
]
