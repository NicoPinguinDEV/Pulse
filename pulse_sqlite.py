"""SQLite helpers shared by the Pulse modules.

``sqlite3.Connection`` used as a context manager only commits or rolls back;
it never closes the connection. Code written as ``with cx() as c:`` therefore
kept file handles and WAL read snapshots open until garbage collection.
``ClosingConnection`` keeps the commit/rollback semantics and closes on exit.
"""

import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(path, timeout: float = 5.0, **kwargs) -> ClosingConnection:
    conn = sqlite3.connect(path, timeout=timeout, factory=ClosingConnection, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn
