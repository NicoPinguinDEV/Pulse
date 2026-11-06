"""Explicitly approved public-facing website content.

This module intentionally stores only the fields meant for publication. It does not sync or expose
Discord IDs, internal roles, moderation records, notes, availability, or dashboard data.
"""
from __future__ import annotations

import base64
import struct
from datetime import datetime, timezone

import pulse_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tables(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS public_team_profiles ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "display_name TEXT NOT NULL, role_label TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '', "
        "avatar_data TEXT NOT NULL DEFAULT '', is_published INTEGER NOT NULL DEFAULT 0, "
        "sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(public_team_profiles)").fetchall()}
    if "avatar_data" not in columns:
        db.execute("ALTER TABLE public_team_profiles ADD COLUMN avatar_data TEXT NOT NULL DEFAULT ''")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_team_profiles_public "
        "ON public_team_profiles(is_published, sort_order, id)"
    )


def _clean(value, label: str, limit: int, *, required: bool = True) -> str:
    result = " ".join(str(value or "").split())
    if required and not result:
        raise ValueError(f"{label}: Bitte einen Wert angeben.")
    if len(result) > limit:
        raise ValueError(f"{label}: Höchstens {limit} Zeichen.")
    return result


def list_profiles(*, published_only: bool = True) -> list[dict]:
    """Return only explicitly public profile fields; never select private Discord/team data."""
    with pulse_db.connect() as db:
        _tables(db)
        where = "WHERE is_published=1" if published_only else ""
        rows = db.execute(
            "SELECT id, display_name, role_label, bio, avatar_data, is_published, sort_order "
            "FROM public_team_profiles " + where +
            " ORDER BY sort_order, display_name COLLATE NOCASE, id"
        ).fetchall()
    return [dict(row) for row in rows]


def create_profile(display_name, role_label, bio="", avatar_data="") -> int:
    display_name = _clean(display_name, "Anzeigename", 40)
    role_label = _clean(role_label, "Teamfunktion", 80)
    bio = _clean(bio, "Kurzbeschreibung", 360, required=False)
    avatar_data = str(avatar_data or "")
    if avatar_data:
        prefix = "data:image/png;base64,"
        if not avatar_data.startswith(prefix) or len(avatar_data) > 700_000:
            raise ValueError("Profilbild: Ungültiges oder zu großes PNG.")
        try:
            raw = base64.b64decode(avatar_data[len(prefix):], validate=True)
            if len(raw) > 512 * 1024 or raw[:8] != bytes.fromhex("89504e470d0a1a0a") or raw[12:16] != b"IHDR":
                raise ValueError
            width, height = struct.unpack(">II", raw[16:24])
            if not (1 <= width <= 1024 and 1 <= height <= 1024):
                raise ValueError
        except (ValueError, struct.error) as exc:
            raise ValueError("Profilbild: Bitte ein gültiges PNG bis 1024 × 1024 Pixel und 512 KB verwenden.") from exc
    now = _now()
    with pulse_db.connect() as db:
        _tables(db)
        order = db.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM public_team_profiles"
        ).fetchone()[0]
        cursor = db.execute(
            "INSERT INTO public_team_profiles(display_name, role_label, bio, avatar_data, is_published, "
            "sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (display_name, role_label, bio, avatar_data, int(order), now, now),
        )
        return int(cursor.lastrowid)


def set_published(profile_id: int, published: bool) -> bool:
    with pulse_db.connect() as db:
        _tables(db)
        changed = db.execute(
            "UPDATE public_team_profiles SET is_published=?, updated_at=? WHERE id=?",
            (1 if published else 0, _now(), int(profile_id)),
        ).rowcount
    return bool(changed)


def delete_profile(profile_id: int) -> bool:
    with pulse_db.connect() as db:
        _tables(db)
        removed = db.execute("DELETE FROM public_team_profiles WHERE id=?", (int(profile_id),)).rowcount
    return bool(removed)
