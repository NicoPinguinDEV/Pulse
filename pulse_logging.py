"""P-10 (Grundlage): central logging with a size-limited rotating file and a ring buffer of recent errors.

* `setup()` (main.py) configures the "pulse" logger once: `logs/pulse.log` (1 MB × 3 files). The console
  output stays the existing print() lines (no duplicate messages: propagation is switched off).
* `report_error()` is used by the new error paths (dashboard errors, slash commands, background tasks
  and jobs). The last 200 errors are kept in memory for the system status – bounded for the RAM budget.
* Never pass tokens, secrets or message contents of users; texts are cut to 300 characters.
"""
from __future__ import annotations

import logging
import os
import re
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "pulse"
MAX_RECENT = 200
_RECENT: deque = deque(maxlen=MAX_RECENT)
_CONFIGURED = False


def setup(base_dir: str | None = None, level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    if _CONFIGURED:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        folder = os.path.join(base_dir or os.getcwd(), "logs")
        os.makedirs(folder, exist_ok=True)
        handler = RotatingFileHandler(os.path.join(folder, "pulse.log"), maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError as exc:
        print(f"⚠️ Log-Datei nicht verfügbar ({type(exc).__name__}) – nur Konsole.")
    try:  # Audit #155: every ERROR record also feeds the error groups (grouped by signature)
        import pulse_ops
        pulse_ops.install_error_handler(LOGGER_NAME)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Fehlergruppen nicht verfügbar ({type(exc).__name__}).")
    _CONFIGURED = True
    return logger


def safe_console() -> None:
    """Emoji in print() must never raise: on a non-UTF-8 console (Windows cp1252) a UnicodeEncodeError
    inside an except block would replace the real error handling. Unencodable characters become '?'."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


class HealthcheckAccessFilter(logging.Filter):
    """Drops successful `/healthz` lines from uvicorn's access log.

    Docker probes `/healthz` every 30 s, so these lines buried real requests and errors in the live log.
    Failed health checks (non-2xx, e.g. 503 during start-up) stay visible.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            path, status = str(args[2]), args[4]
            if path.split("?", 1)[0] == "/healthz" and isinstance(status, int) and 200 <= status < 300:
                return False
        return True


_SENSITIVE_QUERY = re.compile(r"([?&](?:code|state|access_token|refresh_token|token|session|user_session)=)[^&#\s]*", re.IGNORECASE)


def mask_sensitive_query(path: str) -> str:
    """AUTH-6: OAuth `code`/`state` (and token-like parameters) must not end up in the access log, which the
    admin live console can show. Only the values are replaced; the rest of the request line stays readable."""
    return _SENSITIVE_QUERY.sub(r"\1***", str(path))


class SensitiveQueryAccessFilter(logging.Filter):
    """Masks sensitive query values in uvicorn's access-log request line (never drops the line)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            masked = mask_sensitive_query(args[2])
            if masked != args[2]:
                record.args = (*args[:2], masked, *args[3:])
        return True


def quiet_healthcheck_access_log() -> None:
    """Install the access-log filters once on uvicorn's access logger (after uvicorn.Config set it up):
    healthcheck noise reduction and masking of OAuth codes/tokens in query strings (AUTH-6)."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthcheckAccessFilter) for f in access.filters):
        access.addFilter(HealthcheckAccessFilter())
    if not any(isinstance(f, SensitiveQueryAccessFilter) for f in access.filters):
        access.addFilter(SensitiveQueryAccessFilter())


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def report_error(source: str, message: str, error_id: str | None = None) -> None:
    text = str(message)[:300]
    _RECENT.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": str(source)[:80],
                    "message": text, "error_id": error_id or ""})
    get(source).error("%s%s", f"[{error_id}] " if error_id else "", text)


def recent(limit: int = 20) -> list[dict]:
    return list(_RECENT)[-limit:][::-1]
