"""Audit #21: security drills for Raid, AutoMod and Lockdown – a rehearsal that never changes anything.

Every drill works on a private copy of the logic (a fresh ``RaidWatch``, ``match_rules`` on sample text, read-only
checks of the guild) and therefore sends no message, edits no channel, strikes nobody and does not touch the
live Raid watch, AutoMod state or an active lockdown. The result is stored as a report (``security_drills``,
part of the JSON backup) so leadership can see when each protection was last rehearsed and what failed.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import pulse_db
import pulse_raid
from pulse_moderation import match_rules

KINDS = {"raid": "Raid-Alarm", "automod": "AutoMod", "lockdown": "Notfall-Lockdown"}
MAX_REPORTS_PER_GUILD = 200
LOCK_EDIT_PAUSE = 0.7  # mirrors cogs.antinuke.LOCK_EDIT_PAUSE; only used for the time estimate

AUTOMOD_SAMPLES = (
    ("invites", "Tritt unserem Server bei: discord.gg/abcdef", "Einladungslink"),
    ("mentions", "@a @b @c @d @e @f @g @h @i @j", "Massen-Erwähnung"),
    ("caps", "DAS IST EINE SEHR LAUTE NACHRICHT OHNE ENDE", "Großbuchstaben"),
)


def _check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "required": required}


def _record(guild_id, kind: str, actor_id, actor_name, checks: list[dict], summary_extra: str = "") -> dict:
    failed = [c for c in checks if c["required"] and not c["ok"]]
    passed = not failed
    summary = ("Bestanden" if passed else f"{len(failed)} Prüfung(en) fehlgeschlagen") + (f" · {summary_extra}" if summary_extra else "")
    report = {"id": "DRILL-" + secrets.token_hex(3).upper(), "guild_id": str(guild_id), "kind": kind,
              "actor_id": str(actor_id), "actor_name": str(actor_name or "")[:80],
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "passed": 1 if passed else 0,
              "summary": summary[:300], "checks": checks}
    with pulse_db.connect() as db:
        db.execute(
            "INSERT INTO security_drills(id,guild_id,kind,actor_id,actor_name,at,passed,summary,checks_json) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (report["id"], report["guild_id"], kind, report["actor_id"], report["actor_name"], report["at"],
             report["passed"], report["summary"], json.dumps(checks, ensure_ascii=False)))
        db.execute(
            "DELETE FROM security_drills WHERE guild_id=? AND id NOT IN (SELECT id FROM security_drills WHERE guild_id=? "
            "ORDER BY at DESC LIMIT ?)", (report["guild_id"], report["guild_id"], MAX_REPORTS_PER_GUILD))
    return report


def raid_drill(guild_id, actor_id, actor_name) -> dict:
    """Feed synthetic joins into a throw-away RaidWatch and check that every alert level is reachable."""
    checks = []
    for level in pulse_raid.LEVEL_ORDER:
        threshold = pulse_raid.JOIN_LEVELS[level]
        watch = pulse_raid.RaidWatch()
        alert = None
        for index in range(threshold):
            alert = watch.member_joined(1000.0 + index * 0.1, 365.0) or alert
        reached = alert["level"] if alert else None
        checks.append(_check(f"Beitrittswelle {pulse_raid.LEVEL_LABELS[level]}", reached == level,
                             f"{threshold} Beitritte/Minute → " + (pulse_raid.LEVEL_LABELS[alert["level"]] if alert else "kein Alarm")))
    watch = pulse_raid.RaidWatch()
    alert = watch.message(2000.0, 1, 10, "hallo @everyone", 0, True)
    checks.append(_check("@everyone-Versuch", bool(alert and alert["kind"] == "mass_mention"),
                         "Alarm ausgelöst" if alert else "kein Alarm"))
    return _record(guild_id, "raid", actor_id, actor_name, checks, "synthetische Daten, nichts gesendet")


def automod_drill(guild_id, actor_id, actor_name, config: dict) -> dict:
    """Run sample messages through the live AutoMod rules (``config`` is normalized). Nothing is deleted or counted."""
    checks = [_check("AutoMod aktiv", bool(config.get("enabled")), "eingeschaltet" if config.get("enabled") else "ausgeschaltet", required=False),
              _check("Modus", True, f"Modus: {config.get('mode', '?')}", required=False)]
    active = set(config.get("rules") or [])
    for rule, text, label in AUTOMOD_SAMPLES:
        if rule not in active:
            checks.append(_check(label, True, f"Regel „{rule}“ ist nicht aktiv – übersprungen", required=False))
            continue
        hits = match_rules(
            text, rules=config["rules"], mention_count=text.count("@"), mention_limit=config["mention_limit"],
            duplicate_count=0, message_count=1, blocked_terms=config["blocked_terms"],
            blocked_domains=config["blocked_domains"], allowed_domains=config["allowed_domains"],
            caps_percent=config["caps_percent"], caps_min_letters=config["caps_min_letters"],
            duplicate_limit=config["duplicate_limit"], flood_messages=config["flood_messages"])
        names = [name for name, _reason in hits]
        checks.append(_check(label, rule in names, "erkannt: " + ", ".join(names) if names else "nicht erkannt"))
    return _record(guild_id, "automod", actor_id, actor_name, checks, "Beispielnachrichten, nichts gelöscht")


def lockdown_drill(guild, actor_id, actor_name, channel_ids, state: dict, policy: dict) -> dict:
    """Read-only readiness check of the emergency lockdown."""
    ids = [int(c) for c in channel_ids or [] if str(c).isdigit()]
    checks = [_check("Lockdown-Kanäle konfiguriert", bool(ids), f"{len(ids)} Kanal/Kanäle" if ids else "keine ausgewählt")]
    missing = [cid for cid in ids if not hasattr(guild, "get_channel") or guild.get_channel(cid) is None]
    checks.append(_check("Kanäle vorhanden", bool(ids) and not missing, "alle gefunden" if not missing else f"nicht gefunden: {', '.join(map(str, missing))}"))
    perms = getattr(getattr(guild, "me", None), "guild_permissions", None)
    checks.append(_check("Bot-Recht „Rollen verwalten“", bool(getattr(perms, "manage_roles", False)),
                         "vorhanden" if getattr(perms, "manage_roles", False) else "fehlt"))
    checks.append(_check("@everyone-Rolle gefunden", getattr(guild, "default_role", None) is not None, "gefunden"))
    checks.append(_check("Kein Lockdown aktiv", not state.get("active"), "frei" if not state.get("active") else "ein Lockdown ist gerade aktiv"))
    estimate = round(len(ids) * LOCK_EDIT_PAUSE, 1)
    checks.append(_check("Geschätzte Dauer", True, f"ca. {estimate} s für {len(ids)} Kanäle (Discord-Limit-Pause)", required=False))
    minutes = policy.get("default_minutes")
    checks.append(_check("Automatisches Ende", bool(minutes), f"nach {minutes} Minuten" if minutes else "kein Ablauf – Aufheben nur manuell", required=False))
    checks.append(_check("Zweitfreigabe", bool(policy.get("require_approval")),
                         "aktiv" if policy.get("require_approval") else "aus – Start sofort durch eine Person", required=False))
    return _record(guild.id if hasattr(guild, "id") else "", "lockdown", actor_id, actor_name, checks, "nur gelesen, nichts gesperrt")


def recent_reports(guild_id, limit: int = 20) -> list[dict]:
    with pulse_db.connect() as db:
        rows = db.execute("SELECT * FROM security_drills WHERE guild_id=? ORDER BY at DESC LIMIT ?",
                          (str(guild_id), max(1, min(int(limit), 100)))).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["checks"] = json.loads(item.pop("checks_json"))
        except (TypeError, ValueError):
            item["checks"] = []
        result.append(item)
    return result


def last_by_kind(guild_id) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for report in recent_reports(guild_id, 100):
        latest.setdefault(report["kind"], report)
    return latest

