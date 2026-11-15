"""Audit #68: small contextual help next to complicated settings (accessible, no script, one source of texts).

``tip("key")`` renders a native ``<details>`` element: keyboard operable, screen-reader friendly and usable on touch
devices. Unknown keys render nothing, so a missing text can never break a page.
"""
from __future__ import annotations

from html import escape

TOPICS = {
    "widgets": "Wähle, welche Karten auf „Mein Pulse“ erscheinen. Die Reihenfolge und Spaltenzahl stellst du unter „Eigene Dashboard-Layouts“ ein. Ohne Auswahl werden alle Karten gezeigt.",
    "readiness": "Die Kriterien sind Richtwerte für die Beförderungs-Reife (Dienststunden, Tickets, Bewertung, Schulungen, Warnfreiheit, Teamzeit). Pulse zeigt nur einen Hinweis – befördert wird weiterhin von Menschen mit 4-Augen-Prinzip.",
    "schedule_minimums": "Mindestbesetzung je Wochentag und Zeitfenster. Unterschreitet der Dienstplan sie, zeigt Pulse die Lücke an. Es wird niemand automatisch eingeteilt.",
    "shift_reminder": "So viele Minuten vor Schichtbeginn bekommt die eingeplante Person eine Erinnerung. Pro Schicht und Startzeit wird nur einmal erinnert.",
    "workload": "Die Last zählt offene Aufgaben nach Priorität (+1 je überfälliger) und offene Tickets mit je 1,5 Punkten. Ab 6 Punkten „hoch“, ab 10 „überlastet“.",
    "availability": "Durchschnittliche Zahl der Personen im Dienst je Wochentag und Stunde aus den letzten 8 Wochen echter Schichten – eine Prognose, keine Zusage.",
    "anomalies": "Eine Markierung nennt immer die Regel, die sie ausgelöst hat. Sie ist ein Anlass zum Nachsehen, kein Befund über die Person.",
    "performance": "Anteil am Ziel (30 Tage) mit offengelegter Gewichtung. Abwesenheit kürzt das Stundenziel anteilig, fehlende Daten werden ausgelassen, Verwarnungen fließen nicht ein.",
    "live": "Der Status aktualisiert sich alle paar Sekunden, solange der Tab sichtbar ist. Der Aktivitätsfeed zeigt nur Handelnde und Aktion – nie Detailtexte – und ist für die Führung.",
}


def tip(key: str) -> str:
    text = TOPICS.get(key)
    if not text:
        return ""
    return ('<details class="pulse-help" style="display:inline-block;vertical-align:middle;margin-left:6px">'
            '<summary aria-label="Hilfe anzeigen" style="cursor:pointer;display:inline-flex;width:18px;height:18px;align-items:center;justify-content:center;'
            'border-radius:9999px;border:1px solid currentColor;font-size:11px;list-style:none;opacity:.7">?</summary>'
            f'<div role="note" style="max-width:340px;margin-top:6px;padding:8px 10px;border-radius:10px;border:1px solid rgba(148,163,184,.4);font-size:11px;font-weight:400">{escape(text)}</div></details>')
