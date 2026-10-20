"""Pure helpers for validating and retaining exported portfolio PNGs.

No Home Assistant imports so they can be unit-tested on their own.
"""
from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_EXPORT_BYTES = 25 * 1024 * 1024
_EXPORT_NAME_RE = re.compile(r"^export-[0-9a-f]{32}\.png$")
_SHARE_PRUNE_RE = re.compile(
    r"^(export-[0-9a-f]{32}(\.tmp)?\.png|export_result_[0-9a-f]{32}\.json(\.tmp)?)$"
)


class UnsafeExportError(RuntimeError):
    """The app's export result did not point at the expected file."""


def is_valid_export_name(name: str) -> bool:
    return bool(_EXPORT_NAME_RE.match(name))


def read_validated_export(reported_path: str, share_dir: Path, expected_name: str) -> bytes:
    """Return PNG bytes only if reported_path is exactly share_dir/expected_name.

    Rejects other locations, symlinks, non-regular files, oversize files and
    anything that is not a PNG.
    """
    if not is_valid_export_name(expected_name):
        raise UnsafeExportError("Unexpected export file name")
    expected = Path(os.path.realpath(share_dir)) / expected_name
    reported = Path(str(reported_path))
    if reported.name != expected_name or Path(os.path.realpath(reported.parent)) != expected.parent:
        raise UnsafeExportError("Export result points outside the export folder")
    try:
        # O_NOFOLLOW refuses a symlink at the final path component.
        fd = os.open(expected, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as err:
        raise UnsafeExportError(f"Export file cannot be opened safely: {err}") from err
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeExportError("Export file is not a regular file")
        if info.st_size > MAX_EXPORT_BYTES:
            raise UnsafeExportError("Export file is unexpectedly large")
        data = handle.read(MAX_EXPORT_BYTES + 1)
    if not data.startswith(PNG_SIGNATURE):
        raise UnsafeExportError("Export file is not a PNG")
    return data


def write_text_atomic(path: Path, text: str) -> None:
    """Write text so readers see either the old file or the complete new one."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def prune_old_files(directory: Path, max_age_seconds: int, pattern: re.Pattern | None = None, now: float | None = None) -> int:
    """Delete regular files older than max_age_seconds. Returns the count removed."""
    if not directory.is_dir():
        return 0
    now = time.time() if now is None else now
    removed = 0
    for entry in directory.iterdir():
        if pattern is not None and not pattern.match(entry.name):
            continue
        try:
            info = entry.lstat()
            if stat.S_ISREG(info.st_mode) and now - info.st_mtime > max_age_seconds:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def prune_exports(export_dir: Path, share_dir: Path, max_age_seconds: int) -> None:
    prune_old_files(export_dir, max_age_seconds, _EXPORT_NAME_RE)
    prune_old_files(share_dir, max_age_seconds, _SHARE_PRUNE_RE)
