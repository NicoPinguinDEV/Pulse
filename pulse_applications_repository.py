"""JSON-backed repository adapter for Pulse application records.

The adapter deliberately delegates disk reads/writes to webserver's existing
JSON safety helpers. It adds one transaction boundary without introducing a
second storage format or changing the public application mapping.
"""
from __future__ import annotations

import copy
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pulse_application_import import (
    ApplicationImportSnapshot,
    application_import_manifest,
    build_application_import_snapshot,
    export_application_import_payload,
)


class ApplicationRepositoryError(RuntimeError):
    """The application repository cannot safely complete the requested operation."""


class JsonApplicationRepository:
    """Compatibility adapter for the existing application-id to record JSON mapping.

    read_json and write_json must be the application's established,
    safety-aware helpers. The supplied lock must cover both callbacks and all
    repository mutations.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        read_json: Callable[[str, Any], Any],
        write_json: Callable[[str, dict[str, Any]], None],
        lock: Any,
    ) -> None:
        self.path = os.fspath(path)
        self._read_json = read_json
        self._write_json = write_json
        self._lock = lock

    def load(self, default: Any = None) -> Any:
        """Read through the established JSON loader, preserving its fallback behavior."""
        with self._lock:
            return self._read_json(self.path, default)

    def save(self, records: dict[str, Any]) -> None:
        """Persist the legacy mapping through the established atomic/safe JSON writer."""
        if not isinstance(records, dict):
            raise ApplicationRepositoryError("application records must be a mapping object")
        with self._lock:
            self._write_json(self.path, records)

    def mutate(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        """Apply a synchronous read-modify-write while holding the shared file lock.

        The callback receives a private copy. If it raises, no write is attempted.
        It must not perform I/O or await while the lock is held.
        """
        if not callable(change):
            raise TypeError("change must be callable")
        with self._lock:
            current = self._read_json(self.path, {})
            if not isinstance(current, dict):
                raise ApplicationRepositoryError("stored application payload is not a mapping object")
            working = copy.deepcopy(current)
            result = change(working)
            self._write_json(self.path, working)
            return result

    def snapshot(self, *, generation: int = 0, revision: int = 0) -> ApplicationImportSnapshot:
        """Read and validate the exact current JSON bytes without modifying the file."""
        with self._lock:
            payload = Path(self.path).read_bytes()
        return build_application_import_snapshot(
            payload,
            generation=generation,
            revision=revision,
        )

    @staticmethod
    def preview_import(
        payload: bytes | bytearray,
        *,
        generation: int = 0,
        revision: int = 0,
    ) -> ApplicationImportSnapshot:
        """Validate explicitly supplied import bytes; this method never writes them."""
        return build_application_import_snapshot(
            payload,
            generation=generation,
            revision=revision,
        )

    @staticmethod
    def export_snapshot(snapshot: ApplicationImportSnapshot) -> bytes:
        """Return the verified original byte stream without re-serializing JSON."""
        return export_application_import_payload(snapshot)

    @staticmethod
    def snapshot_manifest(snapshot: ApplicationImportSnapshot) -> dict[str, int | str]:
        """Return verified, non-personal snapshot metadata only."""
        return application_import_manifest(snapshot)
