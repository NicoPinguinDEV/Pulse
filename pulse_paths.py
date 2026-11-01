"""Phase 13 (D-06 / SEC R-05): the one place that decides where Pulse keeps its runtime data.

* ``CODE_DIR``: the program (Python modules, ``static/``, bundled ``assets/``). In Docker it lives only in the
  image and is read-only.
* ``DATA_DIR``: everything Pulse writes – SQLite databases, JSON stores, audit logs, uploads, attachments,
  transcripts, backups, the uploaded waiting music (``assets/support_wait.*``), ``.session_secret``, ``logs/``
  and temporary files (``tmp/``).

``PULSE_DATA_DIR`` set → that absolute directory (Docker: ``/data``, on the VPS ``/opt/pulse/data``).
``PULSE_DATA_DIR`` unset → the current working directory, exactly the old behaviour: ``main.prepare_process``
changes into the code folder, so bot hosting without Docker and existing checkouts keep working unchanged, and
tests that change into a temp folder stay isolated.

A configured data directory must carry the marker file written by the migration
(``scripts/pulse_migrate_data.py``) or by a deliberate fresh start (``PULSE_DATA_DIR_INIT=1``). Without it
``verify_data_dir`` refuses the start – an empty or half-migrated mount must never look like a blank server.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ENV_VAR = "PULSE_DATA_DIR"
INIT_ENV_VAR = "PULSE_DATA_DIR_INIT"
MARKER_NAME = ".pulse-data.json"
MARKER_FORMAT = "pulse-data-dir"
# EX_CONFIG from sysexits.h: scripts/pulse-crash-restart.sh does not restart a container that stopped with it.
EXIT_CONFIG = 78

CODE_DIR = Path(__file__).resolve().parent


def _resolve(raw: str | None) -> Path:
    raw = (raw or "").strip()
    return Path(raw).expanduser().resolve() if raw else Path(".")


DATA_DIR = _resolve(os.getenv(ENV_VAR))


def configured() -> bool:
    """True when an explicit data directory is set (Docker/VPS layout)."""
    return bool(os.getenv(ENV_VAR, "").strip())


def data_path(*parts: str) -> Path:
    return DATA_DIR.joinpath(*parts)


def data_file(*parts: str) -> str:
    return str(data_path(*parts))


def read_marker(data_dir=None) -> dict | None:
    path = Path(data_dir if data_dir is not None else DATA_DIR) / MARKER_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("format") == MARKER_FORMAT else None


def write_marker(data_dir, state: str, **extra) -> Path:
    """Atomic: a crash leaves either the old marker or the new one, never a torn file."""
    path = Path(data_dir) / MARKER_NAME
    payload = {"format": MARKER_FORMAT, "version": 1, "state": state,
               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def verify_data_dir(data_dir=None, *, allow_init: bool | None = None) -> list[str]:
    """Problems that must stop the start (empty list = fine). Legacy mode (no PULSE_DATA_DIR) is always fine.

    ``allow_init`` (default: ``PULSE_DATA_DIR_INIT``) writes the marker into a directory that has none – only
    for a deliberately new installation, never instead of the migration of existing data."""
    if data_dir is None:
        if not configured():
            return []
        data_dir = DATA_DIR
    data_dir = Path(data_dir)
    if allow_init is None:
        allow_init = _truthy(os.getenv(INIT_ENV_VAR))
    if not data_dir.is_dir():
        return [f"Datenverzeichnis {data_dir} existiert nicht (Mount fehlt?)."]
    if not os.access(data_dir, os.W_OK | os.X_OK):
        return [f"Datenverzeichnis {data_dir} ist für diesen Benutzer nicht beschreibbar (Besitzer/UID prüfen)."]
    marker = read_marker(data_dir)
    if marker is None:
        if (data_dir / MARKER_NAME).exists():
            return [f"Markierungsdatei {MARKER_NAME} in {data_dir} ist beschädigt – bitte prüfen, nicht löschen."]
        if allow_init:
            write_marker(data_dir, "initialized", note=f"neu angelegt über {INIT_ENV_VAR}")
            return []
        return [f"Datenverzeichnis {data_dir} ist weder migriert noch initialisiert ({MARKER_NAME} fehlt). "
                "Vorhandene Daten erst mit scripts/pulse_migrate_data.py übernehmen; nur bei einer bewusst neuen "
                f"Installation {INIT_ENV_VAR}=1 setzen."]
    state = marker.get("state")
    if state == "in_progress":
        return [f"Die Datenmigration nach {data_dir} wurde nicht abgeschlossen (Zustand in_progress). "
                "Bitte Migration prüfen und erneut ausführen – Pulse startet nicht mit halb kopierten Daten."]
    if state in {"rolled_back", "rollback_in_progress"}:
        return [f"Die Daten in {data_dir} wurden per Rückweg (pulse_migrate_data.py --rollback) in den alten Checkout "
                f"zurückgegeben (Zustand {state}) – dieser Stand ist veraltet. Erst neu migrieren."]
    if state not in {"complete", "initialized"}:
        return [f"Unbekannter Zustand {state!r} in {MARKER_NAME}."]
    return outside_data_dir(data_dir)


# Stores whose location an environment variable can move – in the data-directory layout they must stay inside it,
# otherwise they would land in the read-only code folder or silently outside every backup.
PATH_OVERRIDES = ("PULSE_DB_PATH", "PULSE_AI_MODERATION_CASES_FILE", "PULSE_SOCIAL_PROMO_FILE")


def outside_data_dir(data_dir) -> list[str]:
    root = Path(data_dir).resolve()
    problems = []
    for name in PATH_OVERRIDES:
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        target = Path(raw).resolve()  # relative values resolve against the working directory, like the modules do
        if root != target and root not in target.parents:
            problems.append(f"{name} zeigt außerhalb des Datenverzeichnisses {root} – bitte entfernen oder anpassen.")
    return problems


def ensure_runtime_dirs() -> None:
    """Folders that must exist before logging/tempfile are used. Only in the configured data directory –
    legacy mode keeps creating them lazily as before (A-04)."""
    if configured():
        for name in ("logs", "tmp", "backups"):
            data_path(name).mkdir(parents=True, exist_ok=True)


def describe() -> dict:
    """For the system status – paths only, never file contents."""
    return {"code_dir": str(CODE_DIR), "data_dir": str(DATA_DIR.resolve()), "configured": configured(),
            "marker_state": (read_marker() or {}).get("state") if configured() else None}
