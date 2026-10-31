"""Phase 9: module-level isolation for tests that reach pulse.db or the session secret indirectly.

Sidebar, design and route tests read pulse_db (unread counts, settings) and sign session tokens without
patching the paths – that opened `pulse.db` and created `.session_secret` in the working directory, i.e. the
checkout, which may hold real data. Import both names into a test module:

    from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401

Tests that patch the paths themselves keep working (their patch sits on top of this one).
"""
import tempfile
from pathlib import Path

# Set directly, not with unittest.mock.patch: tests calling patch.stopall() in tearDown would end the isolation.
_STATE: dict = {"tmp": None, "saved": []}


def setUpModule():
    import pulse_db
    import webserver
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    _STATE["tmp"] = tmp
    for module, name, value in ((pulse_db, "DB_PATH", str(Path(tmp.name) / "pulse.db")),
                                (webserver, "SECRET_FILE", str(Path(tmp.name) / ".session_secret"))):
        _STATE["saved"].append((module, name, getattr(module, name)))
        setattr(module, name, value)


def tearDownModule():
    for module, name, value in reversed(_STATE["saved"]):
        setattr(module, name, value)
    _STATE["saved"] = []
    if _STATE["tmp"] is not None:
        _STATE["tmp"].cleanup()
        _STATE["tmp"] = None
