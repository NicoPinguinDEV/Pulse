"""Phase 13.1: container entry point for the Docker Compose integration test (tests/compose_integration.sh).

Runs inside the real compose service (read-only root filesystem, /data bind mount, tmpfs /tmp, .pulse-agent mount)
instead of `python main.py`, so no Discord connection is ever made:

1. tests/container_smoke.py – start checks of main.prepare_process, init_runtime, JSON writes, uploads, transcripts,
   full backup + verify + restore test + restore, commit from PULSE_GIT_SHA, write into /app must fail.
2. Imports every cog module – none may write outside /data at import time.
3. Serves the real FastAPI app (lifespan: init_runtime, background tasks) with a fake, always-ready bot, so the
   compose healthcheck (/healthz) and `docker compose stop` (SIGTERM → graceful shutdown) can be tested.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "tests"))


def main() -> int:
    import container_smoke
    container_smoke.main()

    loaded = []
    for path in sorted((CODE / "cogs").glob("*.py")):
        if path.name != "__init__.py":
            importlib.import_module(f"cogs.{path.stem}")
            loaded.append(path.stem)
    print(f"compose probe: {len(loaded)} Cog-Module importiert", flush=True)

    import uvicorn
    import webserver as ws

    guild = SimpleNamespace(id=ws.GUILD_ID, name="CI", members=[], roles=[], channels=[], text_channels=[],
                            member_count=0, get_member=lambda _id: None, get_role=lambda _id: None,
                            get_channel=lambda _id: None)
    ws.app.state.bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _id: guild, latency=0.05,
                                       is_ws_ratelimited=lambda: False, get_user=lambda _id: None, cogs={},
                                       get_cog=lambda _name: None, user=None, guilds=[guild])
    port = int(os.getenv("SERVER_PORT", "25095"))
    class ProbeServer(uvicorn.Server):
        # Like main.py: SIGTERM only asks the server to stop. uvicorn's own handler re-raises the signal after the
        # shutdown, which would end the process with 143 before the shutdown could be recorded.
        @contextlib.contextmanager
        def capture_signals(self):
            def stop(*_args):
                self.should_exit = True
            previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)

    server = ProbeServer(uvicorn.Config(ws.app, host="0.0.0.0", port=port, log_level="warning", access_log=False))
    print(f"compose probe: Dashboard auf Port {port} (Fake-Bot, keine Discord-Verbindung)", flush=True)
    server.run()  # returns after SIGTERM/SIGINT once the lifespan shut down
    marker = Path(os.environ["PULSE_DATA_DIR"]) / "logs" / "compose-probe.log"
    marker.write_text("shutdown ok\n", encoding="utf-8")
    print("compose probe: shutdown ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
