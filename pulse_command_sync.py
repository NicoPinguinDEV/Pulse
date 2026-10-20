"""P-09: sync slash commands only when the command tree changed.

Discord rate-limits global syncs; syncing on every restart is unnecessary. The hash of the full
command payload is stored in pulse.db. `PULSE_FORCE_COMMAND_SYNC=1` or the admin button in the
system status forces a sync. Commands stay global (no extra guild copy, so nothing appears twice).
"""
from __future__ import annotations

import hashlib
import json
import os

import pulse_db

SETTING_KEY = "command_tree_hash"


def tree_hash(tree) -> str:
    payload = []
    for command in tree.get_commands():
        try:
            payload.append(command.to_dict(tree))
        except TypeError:  # older discord.py signature
            payload.append(command.to_dict())
    payload.sort(key=lambda c: (str(c.get("type", 1)), c.get("name", "")))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def sync_needed(tree) -> tuple[bool, str]:
    current = tree_hash(tree)
    forced = str(os.getenv("PULSE_FORCE_COMMAND_SYNC", "")).strip().lower() in {"1", "true", "yes", "on"}
    return forced or pulse_db.get_setting(SETTING_KEY, "") != current, current


async def sync_commands(tree, *, force: bool = False) -> dict:
    """Returns {'synced': bool, 'count': int, 'reason': str}."""
    needed, current = sync_needed(tree)
    if not (needed or force):
        return {"synced": False, "count": len(tree.get_commands()), "reason": "unverändert"}
    synced = await tree.sync()
    pulse_db.set_setting(SETTING_KEY, current)
    return {"synced": True, "count": len(synced), "reason": "manuell" if force else "geändert"}
