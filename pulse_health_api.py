"""A-01/A-02 step 1: `GET /healthz` in its own module (first route moved out of webserver.py).

The status contract the Docker healthcheck, deploy/remote_deploy.sh and the boot updater rely on is unchanged:
200 only when the bot is connected, the guild is cached and pulse.db answers – otherwise 503. Latency, memory
and job fields are diagnostics only. `webserver.healthz` stays available as a name for existing callers.

Registered flat with `app.add_api_route` instead of `app.include_router(APIRouter)`: since FastAPI 0.162
`include_router` appends one lazy `_IncludedRouter` entry without `.path` to `app.routes`, and Pulse walks
`app.routes` by path in many places (`remove_and_add`, tools/route_map.py, the route/permission tests). Moving
to real routers needs those walkers adapted first.
"""
from __future__ import annotations

import math

from fastapi import Request
from fastapi.responses import JSONResponse

import pulse_db


async def healthz(request: Request):
    import webserver as ws  # at call time: webserver registers this module while it is being imported

    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(ws.GUILD_ID) if bot else None
    db_ok = True
    try:
        with pulse_db.connect() as db:
            db.execute("SELECT 1")
    except Exception:
        db_ok = False

    if not db_ok:
        ws._system_alert("database", "🔴 **Datenbank nicht erreichbar:** `/healthz` konnte `pulse.db` nicht abfragen.")
    ready = bool(bot and bot.is_ready() and guild and db_ok)
    # O-07: diagnostics only – they never change the status code the deploy/Docker health checks rely on.
    latency = getattr(bot, "latency", None) if bot else None
    latency_ms = round(latency * 1000) if isinstance(latency, (int, float)) and math.isfinite(latency) else None
    try:
        from pulse_ultimate import container_memory_mb
        memory_mb = container_memory_mb()
    except Exception:
        memory_mb = None
    try:
        import pulse_jobs
        jobs = pulse_jobs.health_summary()
    except Exception:
        jobs = {"last_job_ok": None, "jobs_failing": []}
    return JSONResponse(
        {
            "ok": ready,
            "version": ws.PULSE_VERSION,
            "bot_ready": bool(bot and bot.is_ready()),
            "guild_ready": bool(guild),
            "database_ok": db_ok,
            "gateway_latency_ms": latency_ms,
            "memory_mb": memory_mb,
            "last_job_ok": jobs["last_job_ok"],
            "jobs_failing": jobs["jobs_failing"],
        },
        status_code=200 if ready else 503,
    )


def register(app) -> None:
    app.add_api_route("/healthz", healthz, methods=["GET"])
