"""Audit #6: persönliches Aktionsmenü (``/menue``) - ein Einstieg für die häufigsten Aktionen aller Bereiche.

Die Daten (Zahlen, passende Befehle, Dashboard-Links) werden hier zusammengestellt; ``cogs/pulse_pro.py`` zeigt sie als
Auswahlmenü mit Link-Buttons. Es werden nur Bereiche und Befehle angeboten, zu denen die Rechte der Person passen. Das Menü führt
selbst nichts aus, es verweist auf die Befehle und Seiten, die ihre eigene Rechteprüfung haben.
"""
from __future__ import annotations

SECTIONS = ("dienst", "aufgaben", "tickets", "termine", "wissen", "profil")
LABELS = {"dienst": "⏱ Dienst", "aufgaben": "✅ Aufgaben & Bestätigungen", "tickets": "🎫 Tickets & Support", "termine": "📅 Termine & Umfragen",
          "wissen": "📖 Wissen & Regeln", "profil": "👤 Profil & Einstellungen"}


def _count(fn) -> int | None:
    try:
        return int(fn())
    except Exception:  # noqa: BLE001 - a missing number never blocks the menu
        return None


def section(key: str, user_id, perms: dict | None, base_url: str = "") -> dict | None:
    """``{title, lines, links: [(label, path)]}`` for one section, or ``None`` for an unknown key."""
    perms = perms or {}
    lead = bool(perms.get("can_promote") or perms.get("is_admin"))
    uid = str(user_id)
    lines: list[str] = []
    links: list[tuple[str, str]] = []
    if key == "dienst":
        lines += ["`/dienst start`, `/dienst pause`, `/dienst ende` – Schicht steuern", "`/dienst status` – deine Zeiten dieser Woche", "`/dienst notiz` – Notiz zur laufenden Schicht"]
        links += [("Mein Pulse", "/me"), ("Dienstplan", "/team/planung?ansicht=verfuegbarkeit")] if lead else [("Mein Pulse", "/me")]
    elif key == "aufgaben":
        import pulse_ack
        import pulse_db
        tasks = _count(lambda: sum(1 for t in pulse_db.list_tasks(assignee_id=uid, limit=200) if t.get("status") not in ("done", "archived")))
        acks = _count(lambda: len(pulse_ack.pending_for(uid)))
        lines += [f"Offene Aufgaben: **{'?' if tasks is None else tasks}** – `/taskdone` schließt eine ab",
                  f"Offene Bestätigungen: **{'?' if acks is None else acks}** – `/bestaetigungen`"]
        if perms.get("can_manage_tasks") or lead:
            lines.append("`/teamtask` – Aufgabe für ein Teammitglied anlegen")
        links += [("Aufgaben", "/tasks"), ("Bestätigungen", "/bestaetigungen")]
    elif key == "tickets":
        manage = bool(perms.get("can_manage_tickets") or lead)
        import pulse_db
        claimed = _count(lambda: len([t for t in pulse_db.list_tickets(claimed_by_id=uid, open_only=True, limit=200)])) if manage else None
        lines += ["Neues Ticket: im Ticket-Panel des Servers eröffnen"] + ([f"Von dir übernommen und offen: **{'?' if claimed is None else claimed}**", "`/ticket notiz`, `/ticket verknuepfen` im Ticketkanal"] if manage else [])
        links += [("Tickets", "/tickets")] if manage else []
    elif key == "termine":
        lines += ["`/selbstservice` – eigene Termine und Zusagen", "`/terminkarte` – Karte mit Zu-/Absage-Buttons (Führung)" if lead else "Zu- und Absagen über die Buttons auf der Terminkarte",
                  "`/umfrage` – Team-Umfrage starten (Führung)" if lead else "Abstimmen über die Buttons auf der Umfragekarte"]
        links += [("Kalender", "/calendar"), ("Besprechungen", "/besprechungen"), ("Abstimmungen", "/ultimate/polls")]
    elif key == "wissen":
        lines += ["`/regelwerk <Stichwort>` – Stelle im Regelwerk mit Quelle", "`/pulse-hilfe` – Befehle, die zu deinen Rechten passen"]
        links += [("Regelwerk-Suche", "/regelwerk-suche"), ("Team-Wiki", "/wiki"), ("Academy", "/academy")]
    elif key == "profil":
        lines += ["`/profil` – dein Profil mit Teamrolle und Zeiten", "`/pulse-sprache` – Sprache einstellen", "`/meine-benachrichtigungen` – deine Hinweise, Einstellungen im Dashboard"]
        links += [("Benachrichtigungen", "/pulse-inbox/settings"), ("Mein Pulse", "/me")]
    else:
        return None
    base = str(base_url or "").rstrip("/")
    return {"title": LABELS[key], "lines": lines, "links": [(label, base + path) for label, path in links if base]}


def available_sections(perms: dict | None) -> list[str]:
    """All sections are personal; ``tickets`` is shown to everybody (opening an own ticket) - nothing leadership-only hides a section."""
    return list(SECTIONS)
