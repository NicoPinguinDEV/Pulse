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
    "ack": "Eine Bestätigung ist nur eine Lesequittung: Du bestätigst selbst, dass du den Beitrag gelesen hast. Niemand kann für dich bestätigen, und sie entscheidet nichts. Führungskräfte sehen, wer noch fehlt.",
    "meeting": "Planen → starten → abschließen. Beschlüsse sind nur in einer laufenden Besprechung möglich; mit Discord-ID entsteht daraus eine Aufgabe. Nach dem Abschluss ist das Protokoll gesperrt.",
    "quiz": "Änderungen gelten nur für künftige Versuche; bestehende Ergebnisse und Zertifikate bleiben unverändert. Die Richtig-Quote zeigt, welche Fragen oft falsch beantwortet werden – ein Hinweis auf unklare Fragen oder Lernbedarf.",
    "rights_target": "Der Soll-Stand friert die aktuellen Rechte aller Teammitglieder ein. Spätere Änderungen erscheinen je Person und Recht als „fehlt“ oder „zusätzlich“. Pulse ändert dabei keine Rolle.",
    "realm": "IC = im Spiel (Ingame-Regelwerk), OOC = außerhalb des Spiels (Discord-Regelwerk). Ein Vorgang gehört genau zu einem Bereich. Ältere Vorgänge bleiben „nicht zugeordnet“, bis sie jemand nachträgt.",
    "automation_event": "Ein Pulse-Ereignis startet die Automation, sobald im gewählten Bereich (und, wenn angegeben, mit dieser Aktion) etwas passiert. Die Automation erhält keine Inhalte, nur Bereich, Aktion und Auslöser. Von Automationen selbst erzeugte Ereignisse starten keine weitere.",
    "automation_depends": "Die Automation startet erst, wenn die gewählte andere in den letzten Minuten erfolgreich lief (Testläufe zählen nicht). Bis dahin wird bei jedem Versuch übersprungen.",
    "testmode": "Der Testmodus wird nur über die Umgebung eingeschaltet (nicht hier). Er erlaubt ausschließlich den Test-Guild und legt gekennzeichnete Testdaten an.",
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
