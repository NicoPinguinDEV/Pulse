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


class ApplicationRepositoryConflict(ApplicationRepositoryError):
    """The application changed while an external action was in progress."""


_MISSING = object()


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

    def update_entry(
        self,
        application_id: str,
        *,
        expected_status: str,
        before: dict[str, Any],
        updated: dict[str, Any],
    ) -> None:
        """Merge one decision's field changes into the newest record.

        The expected status is an optimistic concurrency guard. Unchanged
        fields remain owned by concurrent writers; newly appended history
        entries are merged instead of replacing the full history array.
        """
        key = str(application_id or "").strip()
        if not key or not isinstance(before, dict) or not isinstance(updated, dict):
            raise ApplicationRepositoryError("application update requires an id and mapping snapshots")

        baseline = copy.deepcopy(before)
        changes = copy.deepcopy(updated)

        def apply(records: dict[str, Any]) -> None:
            current = records.get(key)
            if not isinstance(current, dict):
                raise ApplicationRepositoryConflict("application no longer exists")
            if str(current.get("status") or "") != str(expected_status):
                raise ApplicationRepositoryConflict("application status changed during the decision")

            for field in set(baseline) | set(changes):
                old_value = baseline.get(field, _MISSING)
                new_value = changes.get(field, _MISSING)
                if old_value == new_value:
                    continue

                if field == "history" and isinstance(new_value, list):
                    old_history = old_value if isinstance(old_value, list) else []
                    overlap = 0
                    for size in range(min(len(old_history), len(new_value)), 0, -1):
                        if old_history[-size:] == new_value[:size]:
                            overlap = size
                            break
                    appended = new_value[overlap:]
                    if overlap or not old_history:
                        latest_history = current.get("history")
                        merged_history = copy.deepcopy(latest_history) if isinstance(latest_history, list) else []
                        for event in appended:
                            if event not in merged_history:
                                merged_history.append(copy.deepcopy(event))
                        current["history"] = merged_history
                        continue

                if new_value is _MISSING:
                    current.pop(field, None)
                else:
                    current[field] = copy.deepcopy(new_value)

        self.mutate(apply)

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
