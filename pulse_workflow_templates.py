"""Audit #152: versionierte Vorlagenbibliothek für Automationen (Recruiting, Dienst, Support).

Jede Vorlage ist eine vollständige, vom normalen Editor-Validator geprüfte Automationsdefinition. Sie enthält bewusst keine
Discord-IDs und keine Sicherheits-Auslöser oder -Aktionen. Eine Vorlage wird immer als **ausgeschalteter Entwurf im Testmodus**
übernommen; eine Führungskraft prüft sie im Editor und schaltet sie selbst scharf. Die Vorlagen-Version wird im
Versionsverlauf der Automation vermerkt, damit erkennbar bleibt, aus welcher Fassung sie stammt.
"""
from __future__ import annotations

import pulse_automation_rules as rules

CATEGORIES = {"recruiting": "Recruiting", "dienst": "Dienst", "support": "Support"}


def _t(tid, version, category, name, summary, trigger_type, steps, **extra):
    return {"id": tid, "version": version, "category": category, "name": name, "summary": summary,
            "definition": {"name": name, "trigger_type": trigger_type, "steps": steps, **extra}}


TEMPLATES: list[dict] = [
    _t("recruiting-aktivitaet", 1, "recruiting", "Recruiting: Bewerbungsaktivität melden",
       "Bei jedem Recruiting-Ereignis erhalten Führungskräfte einen Hinweis und es entsteht ein Protokolleintrag.", "domain_event",
       [{"type": "notify_managers", "title": "Neue Bewerbungsaktivität", "body": "Im Recruiting gab es das Ereignis {events}."},
        {"type": "activity_log", "text": "Recruiting-Ereignis {events} (Auslöser: {actor_name})"}],
       trigger_config={"domain": "recruiting", "action": "*", "cooldown_seconds": 300}),
    _t("recruiting-wochenbericht", 1, "recruiting", "Recruiting: Wochenbericht an die Führung",
       "Jeden Montag-Rhythmus (7 Tage) ein Team-Report für die Planung neuer Gespräche.", "interval",
       [{"type": "team_report"}], interval_minutes=10080),
    _t("dienst-inaktivitaet", 1, "dienst", "Dienst: Inaktivitätsbericht",
       "Wöchentlich: Teammitglieder ohne Dienstzeit der letzten 14 Tage.", "interval",
       [{"type": "inactivity_report", "days": 14}], interval_minutes=10080),
    _t("dienst-probezeit", 1, "dienst", "Dienst: Probezeit-Bericht",
       "Täglich: Probezeiten, die bald enden.", "interval", [{"type": "probation_report", "days": 7}], interval_minutes=1440),
    _t("dienst-teamreport", 1, "dienst", "Dienst: Team-Report",
       "Wöchentlicher Überblick über Team und Aktivität.", "interval", [{"type": "team_report"}], interval_minutes=10080),
    _t("dienst-aufgabe-erledigt", 1, "dienst", "Dienst: Erledigte Aufgaben protokollieren",
       "Jede erledigte Aufgabe erzeugt einen Protokolleintrag.", "domain_event",
       [{"type": "activity_log", "text": "Aufgabe erledigt von {actor_name}"}],
       trigger_config={"domain": "task", "action": "completed", "cooldown_seconds": 0}),
    _t("support-sla", 1, "support", "Support: Ticket-Frist überwachen",
       "Alle 15 Minuten prüfen, ob Tickets länger als 30 Minuten unbeantwortet sind.", "interval",
       [{"type": "ticket_sla_alert", "minutes": 30}], interval_minutes=15),
    _t("support-rueckstau", 1, "support", "Support: Rückstau melden",
       "Ab 15 offenen Tickets alle 30 Minuten die Führung informieren.", "interval",
       [{"type": "notify_managers", "title": "Viele offene Tickets", "body": "Der Support hat viele offene Tickets."}],
       interval_minutes=30, condition={"type": "open_tickets_gte", "value": 15}),
    _t("support-ticket-geschlossen", 1, "support", "Support: Geschlossene Tickets protokollieren",
       "Jedes geschlossene Ticket erzeugt einen Protokolleintrag.", "domain_event",
       [{"type": "activity_log", "text": "Ticket geschlossen von {actor_name}"}],
       trigger_config={"domain": "ticket", "action": "closed", "cooldown_seconds": 0}),
]
BY_ID = {t["id"]: t for t in TEMPLATES}


def get(template_id: str) -> dict | None:
    return BY_ID.get(str(template_id or ""))


def draft(template: dict) -> dict:
    """Validated automation value as a disabled test-mode draft (raises ``ValueError`` if a template is broken)."""
    return rules.normalize_automation({**template["definition"], "enabled": False, "test_mode": True}, is_admin=False)


def note(template: dict) -> str:
    return f"Vorlage {template['id']} Version {template['version']}"


def catalog() -> list[dict]:
    return [{"id": t["id"], "version": t["version"], "category": t["category"], "category_label": CATEGORIES[t["category"]],
             "name": t["name"], "summary": t["summary"],
             "description": rules.describe({**draft(t), "steps": draft(t)["steps"]})} for t in TEMPLATES]
