"""Local, low-memory Pulse intelligence tools.

This module intentionally uses deterministic rules instead of a paid AI API.
The simulator and workflow sandbox are read-only: neither executes Discord actions.
"""
import json
import math
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


UTC = timezone.utc
CLOSED_STATUSES = {"closed", "resolved", "done", "archived", "cancelled"}
TRIGGER_TYPES = {
    "ticket_opened", "ticket_overdue", "ticket_closed", "task_overdue",
    "application_submitted", "team_warning", "schedule", "manual",
}
CONDITION_FIELDS = {
    "priority", "status", "category", "age_minutes", "assignee_id",
    "created_by_id", "attempt_count",
}
CONDITION_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "exists"}
ACTION_TYPES = {"notify_assignee", "notify_manager", "audit", "set_priority", "create_task"}
ACTION_KEYS = {"type", "message", "priority", "title", "description", "field", "value"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


def parse_timestamp(value):
    """Parse an ISO timestamp and normalise naive legacy values to UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def ticket_is_overdue(ticket, now=None):
    """Use Pulse's existing SLA windows: urgent 30m, high 4h, normal 24h."""
    status = str(ticket.get("status") or "open").lower()
    if status in CLOSED_STATUSES:
        return False
    opened = parse_timestamp(ticket.get("opened_at"))
    if opened is None:
        return False
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age_minutes = max(0.0, (current.astimezone(UTC) - opened).total_seconds() / 60)
    priority = str(ticket.get("priority") or "normal").lower()
    sla_minutes = {"urgent": 30, "high": 240, "normal": 1440}.get(priority, 1440)
    return age_minutes >= sla_minutes


def memory_state(rss_mb):
    """Classify process memory against Pulse's documented 180/210/230 MB thresholds."""
    if rss_mb is None:
        return {"level": 0, "label": "unavailable", "advice": "RAM-Messwert nicht verfügbar."}
    value = float(rss_mb)
    if value >= 230:
        return {"level": 3, "label": "emergency", "advice": "Notfallreserve: optionale Arbeit pausieren und Diagnose prüfen."}
    if value >= 210:
        return {"level": 2, "label": "critical", "advice": "Kritisch: Cache- und Hintergrundlast prüfen; Kernfunktionen bevorzugen."}
    if value >= 180:
        return {"level": 1, "label": "warning", "advice": "Warnung: RAM-Trend beobachten und unnötige Abfragen vermeiden."}
    return {"level": 0, "label": "normal", "advice": "RAM liegt unter der Warnschwelle."}


def system_health(components):
    """Summarise component states without hiding a failed critical dependency."""
    states = [str(item.get("state", "unknown")).lower() for item in components]
    critical = sum(state == "critical" for state in states)
    warning = sum(state in {"warning", "unknown"} for state in states)
    if critical:
        status = "critical"
    elif warning:
        status = "degraded"
    else:
        status = "operational"
    return {
        "status": status,
        "critical_count": critical,
        "warning_count": warning,
        "component_count": len(states),
    }


def build_findings(metrics):
    """Create actionable, explainable recommendations from measured metrics."""
    findings = []
    if int(metrics.get("ticket_sla_overdue", 0) or 0) > 0:
        count = int(metrics["ticket_sla_overdue"])
        findings.append({
            "priority": "high", "topic": "tickets",
            "title": f"{count} Ticket(s) über dem SLA",
            "why": "Die ältesten offenen Fälle zuerst prüfen und unzugewiesene Tickets übernehmen.",
            "href": "/tickets",
        })
    if int(metrics.get("urgent_tickets", 0) or 0) > 0:
        count = int(metrics["urgent_tickets"])
        findings.append({
            "priority": "high", "topic": "tickets",
            "title": f"{count} dringende offene(s) Ticket(s)",
            "why": "Dringende Fälle priorisieren und bei fehlender Zuständigkeit die Supportleitung informieren.",
            "href": "/tickets",
        })
    if int(metrics.get("overdue_tasks", 0) or 0) > 0:
        count = int(metrics["overdue_tasks"])
        findings.append({
            "priority": "high", "topic": "tasks",
            "title": f"{count} überfällige Aufgabe(n)",
            "why": "Fristen, Zuständigkeit und realistische neue Termine überprüfen.",
            "href": "/ultimate/tasks",
        })
    rss = metrics.get("rss_mb")
    if rss is not None and float(rss) >= 230:
        findings.append({
            "priority": "critical", "topic": "system",
            "title": f"RAM-Notfallreserve bei {float(rss):.1f} MB",
            "why": "Das 250-MB-Hostinglimit liegt nahe. Optionale Arbeit drosseln und aktuelle Logs prüfen.",
            "href": "/ultimate/system-twin",
        })
    elif rss is not None and float(rss) >= 210:
        findings.append({
            "priority": "high", "topic": "system",
            "title": f"RAM kritisch bei {float(rss):.1f} MB",
            "why": "Cache-Bereinigung und Garbage Collection sind Schutzmaßnahmen, keine harte RAM-Grenze.",
            "href": "/ultimate/system-twin",
        })
    elif rss is not None and float(rss) >= 180:
        findings.append({
            "priority": "medium", "topic": "system",
            "title": f"RAM-Warnschwelle bei {float(rss):.1f} MB erreicht",
            "why": "RAM-Verlauf kontrollieren, bevor zusätzliche optionale Hintergrundlast aktiviert wird.",
            "href": "/ultimate/system-twin",
        })
    if int(metrics.get("automation_failures_24h", 0) or 0) > 0:
        count = int(metrics["automation_failures_24h"])
        findings.append({
            "priority": "medium", "topic": "automation",
            "title": f"{count} Automationsfehler in 24 Stunden",
            "why": "Die letzten Ausführungsprotokolle öffnen und Ursache sowie Wiederholungsversuche prüfen.",
            "href": "/ultimate/automations",
        })
    if int(metrics.get("open_tickets", 0) or 0) > 0 and int(metrics.get("active_staff", 0) or 0) == 0:
        findings.append({
            "priority": "high", "topic": "team",
            "title": "Offene Tickets, aber kein Teammitglied erkannt",
            "why": "Prüfen, ob Supporter verfügbar sind und ob die konfigurierten Teamrollen stimmen.",
            "href": "/team",
        })
    if int(metrics.get("open_cases", 0) or 0) > 0:
        findings.append({
            "priority": "medium", "topic": "cases",
            "title": f"{int(metrics['open_cases'])} offene TeamOS-Fälle",
            "why": "Offene Fälle nach Priorität und Zuständigkeit durchgehen.",
            "href": "/ultimate/cases",
        })
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda item: rank.get(item["priority"], 9))
    return findings


def build_copilot_report(question, metrics):
    """Return a grounded local report; question text is never executed or sent externally."""
    query = str(question or "").strip()[:300]
    lowered = query.casefold()
    if any(word in lowered for word in ("ticket", "support", "warteschlange", "sla")):
        focus = "tickets"
        summary = (
            f"Ich sehe {int(metrics.get('open_tickets', 0) or 0)} offene Tickets, "
            f"{int(metrics.get('urgent_tickets', 0) or 0)} davon dringend und "
            f"{int(metrics.get('ticket_sla_overdue', 0) or 0)} über dem SLA."
        )
    elif any(word in lowered for word in ("ram", "speicher", "performance", "system", "fehler", "störung")):
        focus = "system"
        rss = metrics.get("rss_mb")
        summary = (
            f"Der Prozess verbraucht {float(rss):.1f} MB RAM."
            if rss is not None else "Für den Prozess liegt gerade kein RAM-Messwert vor."
        )
        summary += f" In den letzten 24 Stunden wurden {int(metrics.get('automation_failures_24h', 0) or 0)} Automationsfehler gezählt."
    elif any(word in lowered for word in ("team", "mitarbeiter", "dienst", "personal")):
        focus = "team"
        summary = (
            f"Pulse erkennt {int(metrics.get('active_staff', 0) or 0)} Teammitglieder, "
            f"{int(metrics.get('team_online', 0) or 0)} davon online, sowie "
            f"{int(metrics.get('active_shifts', 0) or 0)} aktive Schicht(en)."
        )
    elif any(word in lowered for word in ("aufgabe", "frist", "deadline", "task")):
        focus = "tasks"
        summary = f"Es wurden {int(metrics.get('overdue_tasks', 0) or 0)} überfällige Aufgabe(n) erkannt."
    else:
        focus = "overview"
        summary = (
            f"Überblick: {int(metrics.get('open_tickets', 0) or 0)} offene Tickets, "
            f"{int(metrics.get('overdue_tasks', 0) or 0)} überfällige Aufgaben, "
            f"{int(metrics.get('open_cases', 0) or 0)} offene TeamOS-Fälle."
        )
    findings = build_findings(metrics)
    focused = [item for item in findings if item["topic"] in {focus, "system", "automation"}]
    if not focused:
        focused = findings
    if not focused:
        next_steps = [{
            "priority": "low", "topic": "overview",
            "title": "Keine dringende Regelwarnung erkannt",
            "why": "Das ist kein Beweis dafür, dass alle Vorgänge erledigt sind. Öffne bei Bedarf die Live-Übersicht und prüfe die Quelldaten.",
            "href": "/ultimate",
        }]
    else:
        next_steps = focused[:5]
    return {
        "question": query,
        "focus": focus,
        "summary": summary,
        "metrics": dict(metrics),
        "recommendations": next_steps,
        "mode": "local_rules_no_external_ai",
    }


def simulate_scenario(scenario, impact, metrics):
    """Project a hypothetical load/capacity change without mutating live state."""
    allowed = {"ticket_surge", "staff_absence", "ram_spike"}
    if scenario not in allowed:
        raise ValueError("Unbekanntes Szenario.")
    value = float(impact)
    if not math.isfinite(value):
        raise ValueError("Die Auswirkung muss eine endliche Zahl sein.")
    current_tickets = max(0, int(metrics.get("open_tickets", 0) or 0))
    staff = max(0, int(metrics.get("active_staff", 0) or 0))
    if scenario == "ticket_surge":
        extra = max(0, min(1000, int(value)))
        projected = current_tickets + extra
        load = math.ceil(projected / max(staff, 1))
        status = "critical" if projected >= 25 or (staff and load >= 5) else "warning" if projected >= 10 or (staff and load >= 3) else "normal"
        return {
            "scenario": scenario, "title": "Zusätzliche Tickets",
            "current": current_tickets, "projected": projected,
            "detail": f"{extra} zusätzliche Tickets; bei {staff} erkannten Teammitgliedern etwa {load} offene Tickets je Person.",
            "status": status,
            "advice": "Vorher die Bereitschaft erhöhen oder eine Triage-Regel definieren; die Simulation ändert keine Tickets.",
        }
    if scenario == "staff_absence":
        absent_pct = max(0, min(100, value))
        available = int(staff * (100 - absent_pct) / 100)
        load = math.ceil(current_tickets / available) if available else (current_tickets if current_tickets else 0)
        status = "critical" if current_tickets and available == 0 else "warning" if current_tickets and load >= 3 else "normal"
        return {
            "scenario": scenario, "title": "Team-Abwesenheit",
            "current": staff, "projected": available,
            "detail": f"{absent_pct:.0f}% Abwesenheit: geschätzt {available} von {staff} Teammitgliedern verfügbar; etwa {load} offene Tickets je verfügbarer Person.",
            "status": status,
            "advice": "Vertretung und Zuständigkeiten klären. Pulse verschiebt oder pingt in dieser Simulation niemanden.",
        }
    rss = metrics.get("rss_mb")
    if rss is None:
        return {
            "scenario": scenario, "title": "Zusätzlicher RAM-Verbrauch",
            "current": None, "projected": None, "detail": "Ohne aktuellen RAM-Messwert ist keine verlässliche Projektion möglich.",
            "status": "unknown", "advice": "Erst den Live-Systemmonitor prüfen.",
        }
    increase = max(0.0, min(100.0, value))
    projected = float(rss) + increase
    level = memory_state(projected)
    return {
        "scenario": scenario, "title": "Zusätzlicher RAM-Verbrauch",
        "current": round(float(rss), 1), "projected": round(projected, 1),
        "detail": f"Bei +{increase:.1f} MB läge der Prozess bei {projected:.1f} MB von 250 MB Hostingbudget.",
        "status": "critical" if level["level"] >= 2 else "warning" if level["level"] == 1 else "normal",
        "advice": level["advice"] + " Die Simulation pausiert keine Module.",
    }


def validate_workflow_json(raw):
    """Validate a constrained workflow draft; this function never executes actions."""
    source = str(raw or "")
    if len(source) > 20000:
        return False, ["Der Entwurf ist größer als 20.000 Zeichen."], None
    try:
        payload = json.loads(source)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False, ["Der Inhalt ist kein gültiges JSON."], None
    if not isinstance(payload, dict):
        return False, ["Der oberste JSON-Wert muss ein Objekt sein."], None
    errors = []
    unknown = sorted(set(payload) - {"schema_version", "name", "trigger", "interval_minutes", "conditions", "actions", "enabled"})
    if unknown:
        errors.append("Unbekannte Felder: " + ", ".join(unknown))
    if payload.get("schema_version", 1) != 1:
        errors.append("schema_version muss 1 sein.")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        errors.append("name muss ein nicht leerer Text mit höchstens 120 Zeichen sein.")
    if payload.get("trigger") not in TRIGGER_TYPES:
        errors.append("trigger muss einem unterstützten Ereignis entsprechen.")
    if payload.get("trigger") == "schedule":
        try:
            interval = int(payload.get("interval_minutes", 0))
            if not 5 <= interval <= 10080:
                errors.append("interval_minutes muss zwischen 5 und 10080 liegen.")
        except (TypeError, ValueError):
            errors.append("interval_minutes muss eine ganze Zahl sein.")
    conditions = payload.get("conditions", [])
    if not isinstance(conditions, list) or len(conditions) > 20:
        errors.append("conditions muss eine Liste mit höchstens 20 Einträgen sein.")
    else:
        for index, condition in enumerate(conditions, 1):
            if not isinstance(condition, dict):
                errors.append(f"condition #{index} muss ein Objekt sein.")
                continue
            if condition.get("field") not in CONDITION_FIELDS:
                errors.append(f"condition #{index}: field wird nicht unterstützt.")
            if condition.get("operator") not in CONDITION_OPERATORS:
                errors.append(f"condition #{index}: operator wird nicht unterstützt.")
            if "value" not in condition and condition.get("operator") != "exists":
                errors.append(f"condition #{index}: value fehlt.")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 20:
        errors.append("actions muss eine Liste mit 1 bis 20 Aktionen sein.")
    else:
        for index, action in enumerate(actions, 1):
            if not isinstance(action, dict):
                errors.append(f"action #{index} muss ein Objekt sein.")
                continue
            unknown_action_keys = sorted(set(action) - ACTION_KEYS)
            if unknown_action_keys:
                errors.append(f"action #{index}: unbekannte Felder: " + ", ".join(unknown_action_keys))
            action_type = action.get("type")
            if action_type not in ACTION_TYPES:
                errors.append(f"action #{index}: type wird nicht unterstützt.")
            elif action_type in {"notify_assignee", "notify_manager", "audit"}:
                if not isinstance(action.get("message"), str) or not action["message"].strip():
                    errors.append(f"action #{index}: message darf nicht leer sein.")
            elif action_type == "set_priority" and action.get("priority") not in VALID_PRIORITIES:
                errors.append(f"action #{index}: priority ist ungültig.")
            elif action_type == "create_task":
                if not isinstance(action.get("title"), str) or not action["title"].strip():
                    errors.append(f"action #{index}: title darf nicht leer sein.")
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        errors.append("enabled muss true oder false sein.")
    if errors:
        return False, errors, None
    return True, [], payload


def collect_snapshot(request):
    """Collect bounded operational metrics with isolated error handling per source."""
    import pulse_db as db
    import pulse_ultimate as ultimate
    import webserver

    now = datetime.now(UTC)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(webserver.GUILD_ID) if bot else None
    metrics = {
        "generated_at": now.isoformat(timespec="seconds"),
        "version": getattr(webserver, "PULSE_VERSION", "unknown"),
        "bot_ready": bool(bot and bot.is_ready()),
        "guild_ready": bool(guild),
        "primary_db_ok": False,
        "ultimate_db_ok": False,
        "automation_loop_alive": bool(ultimate._AUTOMATION_TASK and not ultimate._AUTOMATION_TASK.done()),
        "open_tickets": 0,
        "urgent_tickets": 0,
        "ticket_sla_overdue": 0,
        "overdue_tasks": 0,
        "open_cases": 0,
        "automations_enabled": 0,
        "automation_failures_24h": 0,
        "active_staff": 0,
        "team_online": 0,
        "active_shifts": 0,
        "rss_mb": ultimate.process_rss_mb(),
        "ram_peak_mb": ultimate.process_peak_rss_mb(),
        "disk_free_mb": None,
        "backup_count": 0,
        "backup_last": None,
    }
    try:
        with db.connect() as connection:
            connection.execute("SELECT 1").fetchone()
            metrics["primary_db_ok"] = True
            tickets = [dict(row) for row in connection.execute(
                "SELECT * FROM tickets WHERE lower(coalesce(status,'')) NOT IN ('closed','resolved','done','archived','cancelled') "
                "ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, opened_at ASC LIMIT 2000"
            ).fetchall()]
            metrics["open_tickets"] = int(connection.execute(
                "SELECT COUNT(*) FROM tickets WHERE lower(coalesce(status,'')) NOT IN ('closed','resolved','done','archived','cancelled')"
            ).fetchone()[0])
            metrics["urgent_tickets"] = int(connection.execute(
                "SELECT COUNT(*) FROM tickets WHERE lower(coalesce(status,'')) NOT IN ('closed','resolved','done','archived','cancelled') AND lower(priority)='urgent'"
            ).fetchone()[0])
            metrics["ticket_sla_overdue"] = sum(ticket_is_overdue(ticket, now) for ticket in tickets)
            tasks = [dict(row) for row in connection.execute(
                "SELECT due_at,status FROM tasks WHERE due_at IS NOT NULL AND lower(coalesce(status,'')) NOT IN ('done','archived') ORDER BY due_at ASC LIMIT 1000"
            ).fetchall()]
            metrics["overdue_tasks"] = sum(
                1 for task in tasks
                if (parse_timestamp(task.get("due_at")) is not None and parse_timestamp(task.get("due_at")) < now)
            )
    except Exception as exc:
        metrics["primary_db_error"] = type(exc).__name__
    try:
        with ultimate.cx() as connection:
            connection.execute("SELECT 1").fetchone()
            metrics["ultimate_db_ok"] = True
            metrics["open_cases"] = int(connection.execute(
                "SELECT COUNT(*) FROM ultimate_cases WHERE lower(coalesce(status,'')) NOT IN ('resolved','closed','done','archived')"
            ).fetchone()[0])
            metrics["automations_enabled"] = int(connection.execute(
                "SELECT COUNT(*) FROM ultimate_automations WHERE enabled=1"
            ).fetchone()[0])
            since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
            metrics["automation_failures_24h"] = int(connection.execute(
                "SELECT COUNT(*) FROM ultimate_automation_runs WHERE status='error' AND started_at>=?",
                (since,),
            ).fetchone()[0])
    except Exception as exc:
        metrics["ultimate_db_error"] = type(exc).__name__
    if guild is not None:
        try:
            members = ultimate.team_members(guild)
            metrics["active_staff"] = len(members)
            metrics["team_online"] = sum(str(member.status) in {"online", "idle", "dnd"} for member in members)
        except Exception as exc:
            metrics["team_metrics_error"] = type(exc).__name__
        try:
            metrics["active_shifts"] = len(webserver.load_shifts().get("active_shifts", {}))
        except Exception:
            pass
    try:
        base = Path(webserver.BASE_DIR)
        metrics["disk_free_mb"] = round(shutil.disk_usage(base).free / (1024 * 1024), 1)
    except (OSError, AttributeError):
        pass
    try:
        backup_dir = Path(webserver.BASE_DIR) / str(webserver.BACKUP_DIR)
        backups = [path for path in backup_dir.iterdir() if path.is_file()] if backup_dir.is_dir() else []
        backups.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        metrics["backup_count"] = len(backups)
        if backups:
            metrics["backup_last"] = datetime.fromtimestamp(backups[0].stat().st_mtime, UTC).isoformat(timespec="seconds")
    except OSError:
        pass
    return metrics


def build_components(metrics, warning_roles=None):
    """Describe system dependencies and what breaks if a dependency is unavailable."""
    items = [
        {
            "name": "Discord Gateway", "state": "ok" if metrics.get("bot_ready") else "critical",
            "detail": "Gateway-Verbindung und Bot-Events",
            "impact": "Voice-Support, Moderation, Rollenaktionen und Discord-Tickets sind betroffen.",
        },
        {
            "name": "BOCHUM-RP-Server", "state": "ok" if metrics.get("guild_ready") else "critical",
            "detail": "Serverobjekt im Bot-Cache",
            "impact": "Mitglieder-, Rollen- und Kanaloperationen können nicht zuverlässig ausgeführt werden.",
        },
        {
            "name": "Pulse-Datenbank", "state": "ok" if metrics.get("primary_db_ok") else "critical",
            "detail": "Tickets, Aufgaben und zentrale SQLite-Daten",
            "impact": "Ticketstatus und zugehörige Aufgaben-/Verwaltungsdaten können ausfallen.",
        },
        {
            "name": "TeamOS-Datenbank", "state": "ok" if metrics.get("ultimate_db_ok") else "critical",
            "detail": "TeamOS-Fälle, Automationen und deren Laufprotokolle",
            "impact": "Command-Center-Fälle und Workflow-Auswertungen sind nicht vollständig verfügbar.",
        },
        {
            "name": "Automationsschleife", "state": "ok" if metrics.get("automation_loop_alive") else "warning",
            "detail": "Laufender Pulse-interner Wartungs- und Automationszyklus",
            "impact": "Geplante Erinnerungen und periodische Prüfungen können verspätet sein.",
        },
    ]
    if warning_roles is not None:
        items.append({
            "name": "Warnrollen", "state": "ok" if warning_roles else "warning",
            "detail": "Konfigurierte Warnrollen im Discord-Server",
            "impact": "Warnstufen könnten nicht korrekt mit Discord-Rollen synchronisiert sein.",
        })
    ram = memory_state(metrics.get("rss_mb"))
    ram_status = "critical" if ram["level"] >= 2 else "warning" if ram["level"] == 1 else "ok"
    items.append({
        "name": "Prozess-RAM", "state": ram_status,
        "detail": f"{metrics['rss_mb']:.1f} MB" if metrics.get("rss_mb") is not None else "Messwert nicht verfügbar",
        "impact": ram["advice"],
    })
    disk = metrics.get("disk_free_mb")
    disk_status = "critical" if disk is not None and disk < 100 else "warning" if disk is not None and disk < 500 else "ok" if disk is not None else "unknown"
    items.append({
        "name": "Datenträger", "state": disk_status,
        "detail": f"{disk:.1f} MB frei" if disk is not None else "Messwert nicht verfügbar",
        "impact": "Wenig Speicher kann Backups, Transkripte, Uploads und Datenbankdateien gefährden.",
    })
    backups = int(metrics.get("backup_count", 0) or 0)
    backup_state = "warning" if backups == 0 else "ok"
    last_backup = parse_timestamp(metrics.get("backup_last"))
    if last_backup is not None and datetime.now(UTC) - last_backup > timedelta(hours=48):
        backup_state = "warning"
    items.append({
        "name": "Backup-Speicher", "state": backup_state,
        "detail": f"{backups} Datei(en); letztes Backup: {metrics.get('backup_last') or 'keines gefunden'}",
        "impact": "Ohne aktuelles Backup ist eine Wiederherstellung nach Datenverlust unsicher.",
    })
    return items


def register(app):
    """Register TeamOS pages once; all expensive checks run only when a user opens them."""
    from fastapi import Cookie, Form, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    import pulse_db as db
    import pulse_ultimate as ultimate

    if getattr(app.state, "pulse_intelligence_registered", False):
        return

    def metric_card(label, value, note=""):
        return ultimate.card(
            f"<div class='tiny'>{ultimate.esc(label)}</div>"
            f"<div class='metric'>{ultimate.esc(value)}</div>"
            f"<div class='tiny'>{ultimate.esc(note)}</div>"
        )

    def findings_html(findings):
        if not findings:
            return "<div class='tiny'>Keine dringenden regelbasierten Hinweise erkannt.</div>"
        rows = []
        for item in findings:
            color = {"critical": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#64748b"}.get(item["priority"], "#64748b")
            rows.append(
                f"<div class='row' style='align-items:flex-start'><div style='min-width:0'>"
                f"<span class='pill' style='border-color:{color};color:{color}'>{ultimate.esc(item['priority'].upper())}</span> "
                f"<b>{ultimate.esc(item['title'])}</b><div class='tiny' style='margin-top:6px'>{ultimate.esc(item['why'])}</div>"
                f"</div><a class='btn' href='{ultimate.esc(item['href'])}'>Öffnen</a></div>"
            )
        return "".join(rows)

    def render_copilot(ctx, metrics, question=""):
        report = build_copilot_report(question, metrics)
        tiles = "".join(metric_card(label, value) for label, value in (
            ("Offene Tickets", metrics.get("open_tickets", 0)),
            ("SLA überfällig", metrics.get("ticket_sla_overdue", 0)),
            ("Offene Aufgaben mit Frist", metrics.get("overdue_tasks", 0)),
            ("Offene TeamOS-Fälle", metrics.get("open_cases", 0)),
        ))
        body = (
            "<div class='grid g4'>" + tiles + "</div>"
            "<div class='grid g2' style='margin-top:13px'>"
            + ultimate.card(
                "<h3>Frag Pulse</h3><p class='tiny'>Regelbasierte Auswertung aktueller Pulse-Daten. "
                "Es wird keine externe KI-API angesprochen. Die Antwort ist eine Empfehlung, keine automatische Aktion.</p>"
                "<form method='post' action='/ultimate/copilot'>"
                f"<input class='input' name='question' maxlength='300' value='{ultimate.esc(question)}' "
                "placeholder='z. B. Warum sind so viele Tickets offen?' required>"
                "<button class='btn primary' style='margin-top:10px'>Analyse starten</button></form>"
            )
            + ultimate.card(
                "<h3>Auswertung</h3><p>" + ultimate.esc(report["summary"]) + "</p>"
                "<div class='tiny'>Modus: lokale Regeln · keine kostenpflichtige KI · keine Änderungen am Server</div>"
            )
            + "</div><div style='margin-top:13px'>"
            + ultimate.card("<h3>Empfohlene nächste Schritte</h3>" + findings_html(report["recommendations"]))
            + "</div>"
        )
        return ultimate.page(ctx, "copilot", "Pulse Copilot", "Erklärbare Empfehlungen aus echten Kennzahlen – ohne externe KI-API.", body)

    def collect_for_request(request):
        return collect_snapshot(request)

    @app.get("/ultimate/copilot", response_class=HTMLResponse)
    async def pulse_copilot_page(request: Request, user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, perm="can_view_dashboard")
        return render_copilot(ctx, collect_for_request(request))

    @app.post("/ultimate/copilot", response_class=HTMLResponse)
    async def pulse_copilot_ask(request: Request, question: str = Form(...), user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, perm="can_view_dashboard")
        question = str(question or "").strip()[:300]
        return render_copilot(ctx, collect_for_request(request), question)

    @app.get("/ultimate/api/copilot")
    async def pulse_copilot_api(request: Request, question: str = "", user_session: str = Cookie(None)):
        ultimate.ctx_auth(request, user_session, perm="can_view_dashboard")
        metrics = collect_for_request(request)
        return JSONResponse({"ok": True, "report": build_copilot_report(question, metrics)})

    @app.get("/ultimate/system-twin", response_class=HTMLResponse)
    async def pulse_system_twin(request: Request, user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, manager=True)
        metrics = collect_for_request(request)
        warning_roles = None
        try:
            import webserver
            if metrics.get("guild_ready"):
                checks = webserver.warning_role_health(ctx.guild, webserver.load_config())
                warning_roles = all(item.get("ok") for item in checks)
        except Exception:
            warning_roles = False
        components = build_components(metrics, warning_roles=warning_roles)
        summary = system_health(components)
        state_label = {"operational": "Betriebsbereit", "degraded": "Eingeschränkt", "critical": "Kritisch"}.get(summary["status"], "Unbekannt")
        state_color = {"operational": "#22c55e", "degraded": "#eab308", "critical": "#ef4444"}.get(summary["status"], "#64748b")
        rows = []
        for component in components:
            color = {"ok": "#22c55e", "warning": "#eab308", "critical": "#ef4444", "unknown": "#94a3b8"}.get(component["state"], "#94a3b8")
            rows.append(
                f"<div class='row' style='align-items:flex-start'><div style='min-width:0;flex:1'>"
                f"<b>{ultimate.esc(component['name'])}</b><div class='tiny'>{ultimate.esc(component['detail'])}</div>"
                f"<div class='tiny' style='margin-top:5px'>Auswirkung: {ultimate.esc(component['impact'])}</div></div>"
                f"<span class='pill' style='border-color:{color};color:{color}'>{ultimate.esc(component['state'].upper())}</span></div>"
            )
        body = (
            ultimate.card(
                f"<div class='tiny'>Gesamtzustand</div><div class='metric' style='color:{state_color}'>{ultimate.esc(state_label)}</div>"
                f"<div class='tiny'>{summary['critical_count']} kritisch · {summary['warning_count']} Warnung(en) / unbekannt · "
                f"{summary['component_count']} Komponenten</div>"
            )
            + "<div class='grid g2' style='margin-top:13px'>"
            + metric_card("RAM aktuell", f"{metrics['rss_mb']:.1f} MB" if metrics.get("rss_mb") is not None else "n. v.", "Warnung ab 180 MB")
            + metric_card("RAM-Spitze", f"{metrics['ram_peak_mb']:.1f} MB" if metrics.get("ram_peak_mb") is not None else "n. v.", "Seit Prozessstart")
            + metric_card("Freier Datenträger", f"{metrics['disk_free_mb']:.1f} MB" if metrics.get("disk_free_mb") is not None else "n. v.")
            + metric_card("Backups", metrics.get("backup_count", 0), metrics.get("backup_last") or "Kein Backup gefunden")
            + "</div><div style='margin-top:13px'>"
            + ultimate.card("<h3>Abhängigkeitskarte</h3>" + "".join(rows))
            + "</div><div style='margin-top:13px'>"
            + ultimate.card(
                "<h3>Was wäre, wenn?</h3><p class='tiny'>Simulationen ändern keine Tickets, Rollen, Schichten oder Systemeinstellungen.</p>"
                "<form method='post' action='/ultimate/simulator/run' class='form'>"
                "<select class='select' name='scenario'><option value='ticket_surge'>Ticketanstieg</option>"
                "<option value='staff_absence'>Team-Abwesenheit (%)</option><option value='ram_spike'>Zusätzlicher RAM (MB)</option></select>"
                "<input class='input' type='number' name='impact' min='0' max='1000' step='1' value='5' required>"
                "<button class='btn primary span2'>Szenario simulieren</button></form>"
                "<div style='margin-top:10px'><a class='btn' href='/ultimate/simulator'>Alle Simulationen öffnen</a></div>"
            ) + "</div>"
        )
        return ultimate.page(ctx, "system_twin", "Pulse System-Zwilling", "Abhängigkeiten, Ressourcen und mögliche Auswirkungen bei Teilausfällen.", body)

    @app.get("/ultimate/api/system-twin")
    async def pulse_system_twin_api(request: Request, user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, manager=True)
        metrics = collect_for_request(request)
        warning_roles = None
        try:
            import webserver
            warning_roles = all(item.get("ok") for item in webserver.warning_role_health(ctx.guild, webserver.load_config()))
        except Exception:
            pass
        components = build_components(metrics, warning_roles)
        return JSONResponse({"ok": True, "health": system_health(components), "metrics": metrics, "components": components})

    def render_simulator(ctx, output=None, selected="ticket_surge", impact=5):
        form = (
            "<form method='post' action='/ultimate/simulator/run' class='form'>"
            "<label>Art des Szenarios<select class='select' name='scenario'>"
            + "".join(
                f"<option value='{value}' {'selected' if selected == value else ''}>{label}</option>"
                for value, label in (
                    ("ticket_surge", "Zusätzliche offene Tickets"),
                    ("staff_absence", "Abwesende Teammitglieder in Prozent"),
                    ("ram_spike", "Zusätzlicher RAM-Verbrauch in MB"),
                )
            )
            + "</select></label><label>Auswirkung<input class='input' name='impact' type='number' step='1' min='0' max='1000' value='{impact}' required></label>"
              "<div class='tiny span2'>Die Eingabe wird pro Szenario begrenzt. Die Simulation ist nur eine Schätzung und führt keinerlei produktive Aktion aus.</div>"
              "<button class='btn primary span2'>Simulation berechnen</button></form>"
        ).replace("{impact}", ultimate.esc(impact))
        body = ultimate.card("<h3>Simulation einstellen</h3>" + form)
        if output:
            color = {"critical": "#ef4444", "warning": "#eab308", "normal": "#22c55e", "unknown": "#94a3b8"}.get(output["status"], "#94a3b8")
            body += ultimate.card(
                f"<h3>Ergebnis: {ultimate.esc(output['title'])}</h3>"
                f"<div class='metric' style='color:{color}'>{ultimate.esc(output['status'].upper())}</div>"
                f"<p>{ultimate.esc(output['detail'])}</p><p class='tiny'>{ultimate.esc(output['advice'])}</p>"
                "<div class='tiny'>Nur Vorschau – im Discord oder in Pulse wurde nichts verändert.</div>"
            )
        return ultimate.page(ctx, "simulator", "Was-wäre-wenn-Simulator", "Belastung und Kapazität vor einer Entscheidung unverbindlich abschätzen.", body)

    @app.get("/ultimate/simulator", response_class=HTMLResponse)
    async def pulse_simulator_page(request: Request, user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, manager=True)
        return render_simulator(ctx)

    @app.post("/ultimate/simulator/run", response_class=HTMLResponse)
    async def pulse_simulator_run(
        request: Request,
        scenario: str = Form(...),
        impact: float = Form(...),
        user_session: str = Cookie(None),
    ):
        ctx = ultimate.ctx_auth(request, user_session, manager=True)
        try:
            metrics = collect_for_request(request)
            output = simulate_scenario(scenario, impact, metrics)
            return render_simulator(ctx, output, scenario, impact)
        except ValueError as exc:
            return render_simulator(ctx, {"title": "Eingabe prüfen", "status": "unknown", "detail": str(exc), "advice": "Bitte ein unterstütztes Szenario auswählen."}, scenario, impact)

    def render_sandbox(ctx, raw="", valid=None, errors=None, normalized=None):
        if not raw:
            raw = json.dumps({
                "schema_version": 1,
                "name": "SLA-Erinnerung",
                "trigger": "ticket_overdue",
                "conditions": [{"field": "priority", "operator": "in", "value": ["high", "urgent"]}],
                "actions": [{"type": "notify_assignee", "message": "Bitte das überfällige Ticket prüfen."}],
                "enabled": False,
            }, ensure_ascii=False, indent=2)
        body = ultimate.card(
            "<h3>Workflow-Entwurf testen</h3><p class='tiny'>Prüft nur Schema, Trigger, Bedingungen und erlaubte Aktionsarten. "
            "Der Entwurf wird weder gespeichert noch aktiviert oder ausgeführt.</p>"
            "<form method='post' action='/ultimate/sandbox' class='form'>"
            f"<textarea class='ta span2' name='workflow_json' maxlength='20000' spellcheck='false' required>{ultimate.esc(raw)}</textarea>"
            "<button class='btn primary span2'>Entwurf validieren</button></form>"
        )
        if valid is not None:
            if valid:
                body += ultimate.card(
                    "<h3>✅ Entwurf strukturell gültig</h3><p>Die Felder passen zum eingeschränkten Sandbox-Schema. "
                    "Das bedeutet ausdrücklich nicht, dass der Workflow bereits produktiv angelegt oder sicher im Live-Betrieb erprobt wurde.</p>"
                    f"<pre style='white-space:pre-wrap;overflow-wrap:anywhere'>{ultimate.esc(json.dumps(normalized, ensure_ascii=False, indent=2))}</pre>"
                )
            else:
                body += ultimate.card(
                    "<h3>⚠️ Prüfung nicht bestanden</h3><ul>"
                    + "".join(f"<li>{ultimate.esc(error)}</li>" for error in (errors or []))
                    + "</ul>"
                )
        return ultimate.page(ctx, "sandbox", "Release-Sandbox", "Workflow-Entwürfe prüfen, bevor sie manuell produktiv konfiguriert werden.", body)

    @app.get("/ultimate/sandbox", response_class=HTMLResponse)
    async def pulse_sandbox_page(request: Request, user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, manager=True)
        return render_sandbox(ctx)

    @app.post("/ultimate/sandbox", response_class=HTMLResponse)
    async def pulse_sandbox_validate(request: Request, workflow_json: str = Form(...), user_session: str = Cookie(None)):
        ctx = ultimate.ctx_auth(request, user_session, manager=True)
        valid, errors, normalized = validate_workflow_json(workflow_json)
        return render_sandbox(ctx, workflow_json[:20000], valid, errors, normalized)

    app.state.pulse_intelligence_registered = True
