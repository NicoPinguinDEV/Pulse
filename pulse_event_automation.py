"""Audit #125: zentrale Ereignisverarbeitung - jedes gespeicherte Pulse-Ereignis kann Automationen starten.

``pulse_db.record_event`` ist der eine Eingang für alle Module (neue ``pulse_events.emit``-Aufrufe und die alten Schreiber).
Nach dem Speichern meldet es das Ereignis hier an. Gibt es keine aktive Automation mit dem Auslöser ``domain_event``,
passiert nichts (ein Wörterbuch-Zugriff, keine Datenbank). Sonst wird ``pulse_ultimate.dispatch_event`` auf dem Bot-Loop
eingeplant - begrenzt, dedupliziert und ohne Rekursion: Ereignisse, die eine Automation selbst auslöst, starten keine weitere.

Die Automation bekommt nur Bereich, Aktion, Akteur und Ereignis-ID (keine Nutzdaten). Fehler hier brechen nie die
auslösende Aktion ab.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import pulse_automation_rules as rules
import pulse_events as events

log = logging.getLogger("pulse.event_automation")
TRIGGER = "domain_event"


def build_context(event_type, target_type, target_id, actor_id, actor_name, event_id) -> dict | None:
    """Kontext für die Automation oder ``None``, wenn dieses Ereignis nie auslösen darf."""
    domain, action = events.classify(event_type, target_type)
    if (domain, "*") in rules.DOMAIN_EVENT_BLOCKED or (domain, action) in rules.DOMAIN_EVENT_BLOCKED:
        return None
    return {"domain": domain, "action": action, "actor_id": str(actor_id or ""), "actor_name": str(actor_name or "")[:100],
            "target_id": str(target_id or "")[:64], "audit_entry_id": str(event_id or ""),
            "events": [f"{domain}.{action}"], "rule_label": events.DOMAINS.get(domain, domain)}


def on_record(event_type, target_type, target_id, actor_id, actor_name, event_id, *, ultimate=None, bot=None, loop=None) -> bool:
    """Gibt ``True`` zurück, wenn ein Lauf eingeplant wurde. Wirft nie."""
    try:
        ultimate = ultimate or sys.modules.get("pulse_ultimate")
        if ultimate is None or TRIGGER not in ultimate._EVENT_TRIGGER_CACHE.get("triggers", ()):
            return False
        if ultimate._DISPATCH_DEPTH.get() > 0:  # caused by an automation step
            return False
        context = build_context(event_type, target_type, target_id, actor_id, actor_name, event_id)
        if context is None:
            return False
        if bot is None:
            ws = sys.modules.get("webserver")
            bot = getattr(getattr(getattr(ws, "app", None), "state", None), "bot", None)
        loop = loop or getattr(bot, "loop", None)
        if bot is None or loop is None or loop.is_closed():
            return False
        import pulse_tasks

        def schedule():
            pulse_tasks.spawn(ultimate.dispatch_event(bot, TRIGGER, context), name="domain-event-automation", loop=loop)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            schedule()
        else:
            loop.call_soon_threadsafe(schedule)
        return True
    except Exception:  # noqa: BLE001 - the audited action must never fail because of an automation
        log.warning("Ereignis-Automation konnte nicht eingeplant werden", exc_info=True)
        return False
