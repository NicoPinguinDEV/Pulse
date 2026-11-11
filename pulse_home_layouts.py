"""Personal named dashboard layouts (#69), stored as per-user Pulse settings.

Layouts only control the order and visibility of known /me widgets. They never change access
permissions, routes, or the underlying team data. Existing home_widgets preferences remain the
fallback for users who have not activated a custom layout.
"""
from __future__ import annotations

import re

import pulse_db as db

LAYOUTS_KEY = "home_custom_layouts"
ACTIVE_KEY = "home_active_layout"
MAX_LAYOUTS = 8
MAX_NAME = 40
_WIDGET_KEYS = ("week", "tickets", "tasks", "waiting", "status", "meeting", "inbox", "shift_notes")
MAX_WIDGETS = len(_WIDGET_KEYS)
_WIDGET_SET = frozenset(_WIDGET_KEYS)
_LAYOUT_ID = re.compile(r"[a-f0-9]{12}")


def _key(base: str, user_id) -> str:
    return f"{base}:{str(user_id)}"


def ordered_widgets(values, positions=None) -> list[str]:
    """Validate and order selected widgets using bounded, user-supplied position numbers."""
    positions = positions if isinstance(positions, dict) else {}
    values = values if isinstance(values, (list, tuple, set, frozenset)) else ()
    selected = []
    for key in _WIDGET_KEYS:
        if key in values and key not in selected:
            selected.append(key)
    def order(key):
        raw = positions.get(key)
        try:
            rank = int(raw)
        except (TypeError, ValueError):
            rank = _WIDGET_KEYS.index(key) + 1
        return max(1, min(MAX_WIDGETS, rank)), _WIDGET_KEYS.index(key)
    return sorted(selected, key=order)


def normalize_layouts(value) -> list[dict]:
    """Return bounded, unique, safe layout records; reject malformed stored entries."""
    if not isinstance(value, (list, tuple)):
        return []
    out, ids, names = [], set(), set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        layout_id = str(raw.get("id") or "").strip()
        name = " ".join(str(raw.get("name") or "").split())
        folded = name.casefold()
        widgets_raw = raw.get("widgets")
        if not _LAYOUT_ID.fullmatch(layout_id) or layout_id in ids:
            continue
        if not name or len(name) > MAX_NAME or any(ord(ch) < 32 for ch in name) or folded in names:
            continue
        if not isinstance(widgets_raw, (list, tuple)):
            continue
        widgets = []
        for item in widgets_raw:
            key = str(item)
            if key in _WIDGET_SET and key not in widgets:
                widgets.append(key)
        if not widgets:
            continue
        try:
            columns = int(raw.get("columns", 2))
        except (TypeError, ValueError):
            columns = 2
        columns = 1 if columns == 1 else 2
        out.append({"id": layout_id, "name": name, "widgets": widgets[:MAX_WIDGETS], "columns": columns})
        ids.add(layout_id)
        names.add(folded)
        if len(out) >= MAX_LAYOUTS:
            break
    return out


def list_layouts(user_id) -> list[dict]:
    return normalize_layouts(db.get_setting(_key(LAYOUTS_KEY, user_id), []))


def active_layout(user_id) -> dict | None:
    layout_id = str(db.get_setting(_key(ACTIVE_KEY, user_id), "") or "")
    if not _LAYOUT_ID.fullmatch(layout_id):
        return None
    return next((item for item in list_layouts(user_id) if item["id"] == layout_id), None)


def save_layout(user_id, name, widgets, *, columns=2, positions=None) -> dict:
    """Create a layout or replace the same user's layout with the same case-insensitive name."""
    name = " ".join(str(name or "").split())
    if not name or len(name) > MAX_NAME or any(ord(ch) < 32 for ch in name):
        raise ValueError(f"Bitte einen Namen mit höchstens {MAX_NAME} Zeichen angeben.")
    selected = ordered_widgets(widgets, positions)
    if not selected:
        raise ValueError("Wähle mindestens ein Widget für dein Layout aus.")
    try:
        columns = int(columns)
    except (TypeError, ValueError):
        columns = 2
    columns = 1 if columns == 1 else 2

    layouts = list_layouts(user_id)
    existing = next((item for item in layouts if item["name"].casefold() == name.casefold()), None)
    if existing is None and len(layouts) >= MAX_LAYOUTS:
        raise ValueError(f"Du kannst höchstens {MAX_LAYOUTS} eigene Layouts speichern.")
    import secrets
    layout_id = existing["id"] if existing else secrets.token_hex(6)
    saved = {"id": layout_id, "name": name, "widgets": selected, "columns": columns}
    if existing:
        layouts = [saved if item["id"] == layout_id else item for item in layouts]
    else:
        layouts.append(saved)
    db.set_setting(_key(LAYOUTS_KEY, user_id), layouts)
    db.set_setting(_key(ACTIVE_KEY, user_id), layout_id)
    return {**saved, "replaced": existing is not None}


def activate_layout(user_id, layout_id) -> dict | None:
    wanted = str(layout_id or "")
    selected = next((item for item in list_layouts(user_id) if item["id"] == wanted), None)
    if selected is None:
        return None
    db.set_setting(_key(ACTIVE_KEY, user_id), selected["id"])
    return selected


def delete_layout(user_id, layout_id) -> dict | None:
    wanted = str(layout_id or "")
    layouts = list_layouts(user_id)
    selected = next((item for item in layouts if item["id"] == wanted), None)
    if selected is None:
        return None
    db.set_setting(_key(LAYOUTS_KEY, user_id), [item for item in layouts if item["id"] != wanted])
    if str(db.get_setting(_key(ACTIVE_KEY, user_id), "") or "") == wanted:
        db.delete_setting(_key(ACTIVE_KEY, user_id))
    return selected
