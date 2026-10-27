"""Path containment for file names that come from a request (downloads, restores, uploads)."""
from __future__ import annotations

import os


def contained_file(base, name) -> str | None:
    """Real path of the plain file name *name* inside *base*, or None if it would leave *base*.

    Directory parts, "..", absolute paths and symlinks pointing outside are all rejected, because the
    resolved path has to start with the resolved base directory.
    """
    root = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(root, os.path.basename(str(name or ""))))
    if not candidate.startswith(root + os.sep):
        return None
    return candidate
