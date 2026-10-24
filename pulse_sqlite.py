"""SQLite helpers shared by the Pulse modules.

``sqlite3.Connection`` used as a context manager only commits or rolls back;
it never closes the connection. Code written as ``with cx() as c:`` therefore
kept file handles and WAL read snapshots open until garbage collection.
``ClosingConnection`` keeps the commit/rollback semantics and closes on exit.

It also counts open connections for the health view (Server Control Center):
currently open, peak, total opened and connections that were only closed by
garbage collection (a leak somewhere). Counting is a few integer updates.
"""

import sqlite3
import threading

_STATS_LOCK = threading.Lock()
_STATS = {"open": 0, "peak": 0, "opened_total": 0, "closed_by_gc": 0}


class ClosingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pulse_open = True
        with _STATS_LOCK:
            _STATS["open"] += 1
            _STATS["opened_total"] += 1
            if _STATS["open"] > _STATS["peak"]:
                _STATS["peak"] = _STATS["open"]

    def _pulse_mark_closed(self, by_gc: bool = False) -> None:
        if not getattr(self, "_pulse_open", False):
            return
        self._pulse_open = False
        with _STATS_LOCK:
            _STATS["open"] -= 1
            if by_gc:
                _STATS["closed_by_gc"] += 1

    def close(self):
        try:
            super().close()
        finally:
            self._pulse_mark_closed()

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

    def __del__(self):
        try:
            if getattr(self, "_pulse_open", False):
                self._pulse_mark_closed(by_gc=True)
                super().close()  # release the file handle now (Windows keeps it locked otherwise)
        except Exception:
            pass


def connect(path, timeout: float = 5.0, **kwargs) -> ClosingConnection:
    conn = sqlite3.connect(path, timeout=timeout, factory=ClosingConnection, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn


def connection_stats() -> dict:
    with _STATS_LOCK:
        return dict(_STATS)
