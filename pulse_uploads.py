"""Memory-friendly upload handling and heap release within Pulse's bounded container budget.

Image uploads (panel images, welcome/leave/verification banners) used to be read
completely into RAM (up to 8 MB each, four per panel save). Here they are checked by
their file signature and written to disk in small chunks instead.
"""
from __future__ import annotations

import ctypes
import os
import secrets
import sys
from pathlib import Path
from typing import Callable

CHUNK_BYTES = 256 * 1024
_LIBC = None


async def save_image_upload(upload, folder: Path, name_for: Callable[[str], str], max_bytes: int, *,
                            too_large: str, wrong_type: str, detect: Callable[[bytes], str | None]) -> tuple[str, str]:
    """Stream an uploaded image to `folder`. Returns (stored file name, "") or ("", error).

    The type comes from the file signature (never the filename); oversized uploads are
    aborted as soon as the limit is passed and the partial file is removed.
    """
    temporary = None
    try:
        header = await upload.read(16)
        ext = detect(header)
        if not ext:
            return "", wrong_type
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / f".{secrets.token_hex(6)}.uploading"
        size = len(header)
        with open(temporary, "wb") as handle:
            handle.write(header)
            while True:
                chunk = await upload.read(CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    handle.close()
                    temporary.unlink(missing_ok=True)
                    temporary = None
                    return "", too_large
                handle.write(chunk)
        name = name_for(ext)
        os.replace(temporary, folder / name)
        temporary = None
        return name, ""
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            await upload.close()
        except Exception:
            pass
        release_freed_memory()


def release_freed_memory() -> bool:
    """Ask glibc to hand freed heap pages back to the OS (Linux only).

    After large temporary allocations Python frees the objects, but glibc keeps the pages,
    so the container's measured RAM stays high. malloc_trim(0) returns them. Cheap, safe,
    and a no-op on Windows/macOS.
    """
    global _LIBC
    if not sys.platform.startswith("linux"):
        return False
    try:
        if _LIBC is None:
            _LIBC = ctypes.CDLL("libc.so.6")
        return bool(_LIBC.malloc_trim(0))
    except (OSError, AttributeError):
        return False
