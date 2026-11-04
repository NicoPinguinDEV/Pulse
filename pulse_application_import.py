"""Pure, side-effect-free integrity contract for Phase 15.6d application imports.

This module accepts explicitly supplied JSON bytes only. It does not open files,
create snapshots on disk, initialize SQLite, or select a runtime backend.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


APPLICATION_IMPORT_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ApplicationImportError(ValueError):
    """An application import payload or snapshot cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ApplicationImportSnapshot:
    """Immutable in-memory description of one exact legacy JSON payload."""

    schema_version: int
    generation: int
    revision: int
    sha256: str
    record_count: int
    payload: bytes


def _counter(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApplicationImportError(f"{name} must be a non-negative integer")
    return value


def _object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ApplicationImportError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ApplicationImportError("JSON contains a non-standard numeric constant")


def _decode_records(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8", errors="strict")
        records = json.loads(
            decoded,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_constant,
        )
    except ApplicationImportError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApplicationImportError("Application JSON payload is not valid strict UTF-8 JSON") from exc

    if not isinstance(records, dict):
        raise ApplicationImportError("Application JSON payload must be a top-level object")
    return records


def build_application_import_snapshot(
    payload: bytes | bytearray,
    *,
    generation: int = 0,
    revision: int = 0,
) -> ApplicationImportSnapshot:
    """Validate explicit JSON bytes and capture immutable import metadata.

    The original bytes are retained verbatim. Unknown application fields, JSON
    whitespace, key order, and Unicode representation are not re-serialized.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes or bytearray")
    normalized_payload = bytes(payload)
    generation_value = _counter(generation, "generation")
    revision_value = _counter(revision, "revision")
    records = _decode_records(normalized_payload)

    return ApplicationImportSnapshot(
        schema_version=APPLICATION_IMPORT_SCHEMA_VERSION,
        generation=generation_value,
        revision=revision_value,
        sha256=hashlib.sha256(normalized_payload).hexdigest(),
        record_count=len(records),
        payload=normalized_payload,
    )


def verify_application_import_snapshot(snapshot: ApplicationImportSnapshot) -> bool:
    """Verify schema, counters, byte digest, and parsed top-level record count."""
    if not isinstance(snapshot, ApplicationImportSnapshot):
        raise ApplicationImportError("application import snapshot has an unsupported type")
    if snapshot.schema_version != APPLICATION_IMPORT_SCHEMA_VERSION:
        raise ApplicationImportError("application import snapshot schema version is unsupported")
    _counter(snapshot.generation, "generation")
    _counter(snapshot.revision, "revision")
    if not isinstance(snapshot.payload, bytes):
        raise ApplicationImportError("application import snapshot payload must be bytes")
    if not isinstance(snapshot.sha256, str) or not _SHA256_RE.fullmatch(snapshot.sha256):
        raise ApplicationImportError("application import snapshot digest is malformed")
    if hashlib.sha256(snapshot.payload).hexdigest() != snapshot.sha256:
        raise ApplicationImportError("application import snapshot digest does not match its payload")
    records = _decode_records(snapshot.payload)
    if isinstance(snapshot.record_count, bool) or not isinstance(snapshot.record_count, int):
        raise ApplicationImportError("application import snapshot record count is malformed")
    if snapshot.record_count != len(records):
        raise ApplicationImportError("application import snapshot record count does not match its payload")
    return True


def application_import_manifest(snapshot: ApplicationImportSnapshot) -> dict[str, int | str]:
    """Return non-personal metadata only, after verifying the in-memory snapshot."""
    verify_application_import_snapshot(snapshot)
    return {
        "schema_version": snapshot.schema_version,
        "generation": snapshot.generation,
        "revision": snapshot.revision,
        "sha256": snapshot.sha256,
        "record_count": snapshot.record_count,
        "payload_bytes": len(snapshot.payload),
    }


def export_application_import_payload(snapshot: ApplicationImportSnapshot) -> bytes:
    """Return the exact source byte stream after a full integrity verification."""
    verify_application_import_snapshot(snapshot)
    return snapshot.payload
