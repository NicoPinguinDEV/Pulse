"""F-08 public status page and F-09 game status – both optional, without personal data.

F-08: support open/closed (voice-support hours), queue length (unclaimed tickets + people in the voice
      waiting room), RP status (last "RP Start/Stop" from the RP panel). Cached for 30 s for everyone.
F-09: Roblox server status via the official public games API (https://games.roblox.com/v1/games), only
      when `PULSE_ROBLOX_UNIVERSE_ID` is set. No key needed; without the variable nothing is requested.
      Uses the existing bounded Roblox HTTP pool of the dashboard, cached for 60 s.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

CACHE_SECONDS = 30.0
GAME_CACHE_SECONDS = 60.0
_cache: dict = {"at": 0.0, "data": None}
_game_cache: dict = {"at": 0.0, "data": None, "universe": ""}


def universe_id() -> str:
    value = os.getenv("PULSE_ROBLOX_UNIVERSE_ID", "").strip()
    return value if value.isdigit() else ""


def parse_game(payload) -> dict | None:
    """Pick the few public fields from the Roblox games API answer."""
    try:
        item = (payload or {}).get("data", [])[0]
    except (AttributeError, IndexError, TypeError):
        return None
    if not isinstance(item, dict):
        return None
    return {"name": str(item.get("name") or "")[:80], "playing": int(item.get("playing") or 0),
            "max_players": int(item.get("maxPlayers") or 0), "visits": int(item.get("visits") or 0)}


async def game_status(client_factory, now: float | None = None) -> dict | None:
    """{"name", "playing", "max_players", "visits"} or None (not configured / API unreachable)."""
    import pulse_flags
    uid = universe_id()
    if not uid or not pulse_flags.enabled("game_status"):
        return None
    now = time.monotonic() if now is None else now
    if _game_cache["universe"] == uid and _game_cache["at"] and now - _game_cache["at"] < GAME_CACHE_SECONDS:
        return _game_cache["data"]
    data = None
    try:
        response = await client_factory().get("https://games.roblox.com/v1/games", params={"universeIds": uid})
        if response.status_code == 200:
            data = parse_game(response.json())
        else:
            print(f"⚠️ Roblox-Spielstatus: HTTP {response.status_code}")
    except Exception as exc:  # network errors: show "nicht erreichbar", try again after the cache time
        print(f"⚠️ Roblox-Spielstatus nicht erreichbar ({type(exc).__name__}).")
    _game_cache.update(at=now, data=data, universe=uid)
    return data


def snapshot(guild, *, voice_cfg: dict, open_tickets: list[dict], voice_waiting: int, rp_status: dict | None,
             now: datetime) -> dict:
    """Public numbers only – no names, IDs or message contents."""
    from pulse_voice_support_utils import support_open_now
    unclaimed = sum(1 for t in open_tickets if not t.get("claimed_by_id"))
    rp = rp_status if isinstance(rp_status, dict) else {}
    return {
        "server": str(getattr(guild, "name", "") or "Server"),
        "support_active": bool((voice_cfg or {}).get("active", True)),
        "support_open": support_open_now(voice_cfg or {}, now),
        "queue_tickets": unclaimed,
        "queue_voice": max(0, int(voice_waiting or 0)),
        "rp_state": rp.get("state") if rp.get("state") in ("start", "stop") else "",
        "rp_since": str(rp.get("at") or ""),
        "rp_next_start": str(rp.get("next_start") or "")[:30],
    }


def cached(builder) -> dict:
    now = time.monotonic()
    if _cache["data"] is None or now - _cache["at"] >= CACHE_SECONDS:
        _cache.update(at=now, data=builder())
    return _cache["data"]


def reset_cache() -> None:
    _cache.update(at=0.0, data=None)
    _game_cache.update(at=0.0, data=None, universe="")
