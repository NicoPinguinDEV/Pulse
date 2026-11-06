"""Impressum, privacy notice, cookie and security pages of the public website.

The texts describe what the code does (checked against the code on 2026-11-06). Operator details come
from the environment and are never invented: while a required value is missing, the page says so, and
the site stays out of search engines. docs/RECHTLICHE_FREIGABE.md lists what a person must still check.
"""
from __future__ import annotations

import os
from html import escape

REQUIRED_FIELDS = {
    "PULSE_LEGAL_NAME": "Vollständiger Name oder Organisation des Anbieters",
    "PULSE_LEGAL_ADDRESS": "Zustellfähige Anschrift (Zeilen mit | trennen)",
    "PULSE_LEGAL_EMAIL": "E-Mail-Adresse",
    "PULSE_LEGAL_HOSTER": "Name und Sitz des Hosting-Anbieters",
}
CONFIG_NOTICE = (
    '<p class="notice">Diese Seite ist noch nicht vollständig: Der Betreiber muss die fehlenden Angaben ergänzen.</p>'
)


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def value(key: str) -> str:
    return os.getenv(key, "").strip()


def missing_fields() -> dict[str, str]:
    return {key: label for key, label in REQUIRED_FIELDS.items() if not value(key)}


def ready() -> bool:
    return not missing_fields()


def _or_missing(key: str) -> str:
    return e(value(key)) if value(key) else f'<span class="missing">{e(REQUIRED_FIELDS[key])} fehlt</span>'


def _address() -> str:
    raw = value("PULSE_LEGAL_ADDRESS")
    return "<br>".join(e(part.strip()) for part in raw.split("|")) if raw else _or_missing("PULSE_LEGAL_ADDRESS")


def _operator() -> str:
    email = value("PULSE_LEGAL_EMAIL")
    mail = f'<a href="mailto:{e(email)}">{e(email)}</a>' if email else _or_missing("PULSE_LEGAL_EMAIL")
    phone = f"<br>Telefon: {e(value('PULSE_LEGAL_PHONE'))}" if value("PULSE_LEGAL_PHONE") else ""
    return f"<p>{_or_missing('PULSE_LEGAL_NAME')}<br>{_address()}</p><p>E-Mail: {mail}{phone}</p>"


def impressum_html() -> str:
    responsible = value("PULSE_LEGAL_CONTENT_RESPONSIBLE")
    extra = value("PULSE_LEGAL_EXTRA")
    return f"""<div class="doc"><h1>Impressum</h1>
{"" if ready() else CONFIG_NOTICE}
<h2>Angaben nach § 5 DDG</h2>{_operator()}
{f"<h2>Inhaltlich verantwortlich</h2><p>{e(responsible)}</p>" if responsible else ""}
{f"<h2>Weitere Angaben</h2><p>{e(extra)}</p>" if extra else ""}
<h2>Hinweis</h2><p>Bochum RP ist eine Roleplay-Community. Spielinhalte sind Fiktion. Links zu Discord führen zu einem externen Dienst, für dessen Inhalte dessen Betreiber verantwortlich ist.</p>
</div>"""


SECTIONS = (
    ("Website aufrufen", "Beim Aufruf verarbeitet der Server technisch Ihre IP-Adresse, den Zeitpunkt, die aufgerufene Seite und die Browserkennung, damit die Seite ausgeliefert und vor Überlastung geschützt werden kann. Zur Begrenzung von Anfragen hält der Server die Adresse kurzzeitig im Arbeitsspeicher.",
     "Art. 6 Abs. 1 lit. f DSGVO (sicherer Betrieb).", "Hosting-Anbieter.",
     "Begrenzungsdaten im Arbeitsspeicher nur für das Zeitfenster. Technische Protokolle rotieren (vier Dateien mit je 1 MB)."),
    ("Team-Login mit Discord", "Der Login nutzt Discord OAuth mit dem Bereich „identify“. Pulse erhält Ihre Discord-ID, Ihren Namen und Ihr Avatar. Für jede Anmeldung werden IP-Adresse, Zeitpunkt und Browserkennung protokolliert, um Konten zu schützen. Nur Administratoren sehen diese Einträge.",
     "Art. 6 Abs. 1 lit. f DSGVO (Kontosicherheit), bei Teammitgliedern zusätzlich Art. 6 Abs. 1 lit. b DSGVO.", "Discord als Anmeldedienst, Hosting-Anbieter.",
     "Anmeldeprotokoll 30 Tage. Sitzungen enden nach 7 Tagen oder nach 24 Stunden ohne Aktivität."),
    ("Bewerbungen", "Das Portal verarbeitet Ihre Discord-Identität, Ihre Antworten, Testergebnisse, Praxisaufgaben sowie den Bearbeitungsstand. Ein Entwurf bleibt in Ihrem Browser, bis Sie ihn absenden.",
     "Art. 6 Abs. 1 lit. b DSGVO (Durchführung der Bewerbung).", "Berechtigte Teammitglieder, Discord für Benachrichtigungen, Hosting-Anbieter.",
     "Die automatische Löschung ist derzeit nicht aktiviert. Bewerbungsdaten bleiben gespeichert, bis sie manuell entfernt werden. Sie können die Löschung jederzeit verlangen."),
    ("Team-Dashboard", "Für Teammitglieder speichert Pulse Dienstzeiten, Aufgaben, Abwesenheiten, Verwarnungen, Notizen und Aktionsprotokolle.",
     "Art. 6 Abs. 1 lit. b und f DSGVO (Organisation des Teams).", "Berechtigte Teammitglieder, Hosting-Anbieter.",
     "Bis zum Ende der Teamzugehörigkeit und danach, solange es zur Nachvollziehbarkeit nötig ist. Die Frist legt der Betreiber fest."),
    ("Discord-Server und Bot", "Der Bot verarbeitet Nachrichten in Support-Kanälen, Tickets, Rollen und Sprachkanal-Ereignisse. Ticket-Transkripte werden derzeit ohne Frist aufbewahrt, Moderationsprotokolle für die Fallbearbeitung.",
     "Art. 6 Abs. 1 lit. f DSGVO (Betrieb und Moderation der Community).", "Discord, berechtigte Teammitglieder.",
     "Ticket-Transkripte bis zur manuellen Löschung. Abgeschlossene KI-Prüffälle 30 Tage."),
    ("KI-Unterstützung", "Wenn der Betreiber die Funktionen aktiviert, gehen Daten an einen externen KI-Anbieter (Google Gemini oder OpenAI): beim Support-Assistenten Ihre Frage, das Regelwerk und bis zu vier vorherige Gesprächsrunden, bei der KI-Moderation der Text geprüfter Nachrichten mit Kanalname, Rollennamen und Erwähnungen. Die KI sanktioniert niemanden, Maßnahmen bestätigt immer ein Mensch. Gesprächsverläufe liegen nur im Arbeitsspeicher, mit /ki_vergessen löschen Sie Ihren eigenen sofort.",
     "Art. 6 Abs. 1 lit. f DSGVO (Support und Schutz der Community).", "Der aktivierte KI-Anbieter als Empfänger, möglicherweise in einem Drittland.",
     "Gesprächsverlauf bis zum Neustart oder /ki_vergessen. Offene KI-Prüffälle bis zur Entscheidung, abgeschlossene 30 Tage. Die Speicherung beim Anbieter richtet sich nach dessen Bedingungen."),
    ("Roblox-Abfragen", "Das Dashboard fragt zur Zuordnung von Teammitgliedern und Moderationsfällen öffentliche Roblox-Profile über die Roblox-Schnittstelle ab.",
     "Art. 6 Abs. 1 lit. f DSGVO.", "Roblox.", "Im Arbeitsspeicher zwischengespeichert, Anfragen werden nicht dauerhaft abgelegt."),
    ("Backups", "Automatische Komplett-Sicherungen enthalten die oben genannten Daten, damit sie nach einem Ausfall wiederhergestellt werden können.",
     "Art. 6 Abs. 1 lit. f DSGVO (Datensicherheit).", "Hosting-Anbieter.",
     "Rollierend: täglich für 7 Tage, wöchentlich für 4 Wochen, monatlich für 6 Monate."),
)


def privacy_html() -> str:
    rows = "".join(
        f"<section><h3>{e(title)}</h3><p>{e(what)}</p><dl><dt>Rechtsgrundlage</dt><dd>{e(basis)}</dd>"
        f"<dt>Empfänger</dt><dd>{e(recipients)}</dd><dt>Speicherdauer</dt><dd>{e(period)}</dd></dl></section>"
        for title, what, basis, recipients, period in SECTIONS
    )
    return f"""<div class="doc"><h1>Datenschutzerklärung</h1>
{"" if ready() else CONFIG_NOTICE}
<p>Hier steht, welche personenbezogenen Daten Bochum RP und das System Pulse verarbeiten, warum und wie lange. Die Beschreibung folgt dem tatsächlichen Verhalten der Software.</p>
<h2>Verantwortlicher</h2>{_operator()}
<h2>Hosting</h2><p>Die Website und der Bot laufen bei: {_or_missing("PULSE_LEGAL_HOSTER")}.</p>
<h2>Verarbeitungen im Einzelnen</h2>{rows}
<h2>Übermittlung in Drittländer</h2><p>Discord und, sofern aktiviert, die KI-Anbieter können Daten außerhalb der EU verarbeiten, insbesondere in den USA. Die jeweils einschlägige Grundlage nach Art. 44 ff. DSGVO richtet sich nach dem Anbieter.</p>
<h2>Cookies und Browser-Speicher</h2><p>Wir verwenden nur technisch erforderliche Speicherung. Die Übersicht steht unter <a href="/cookies">Cookies</a>.</p>
<h2>Ihre Rechte</h2><p>Sie haben das Recht auf Auskunft (Art. 15), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch gegen Verarbeitungen auf Grundlage berechtigter Interessen (Art. 21 DSGVO). Eine erteilte Einwilligung können Sie jederzeit widerrufen (Art. 7 Abs. 3 DSGVO). Für Ihr Anliegen genügt eine Nachricht an die oben genannte Adresse.</p>
<p>Sie können sich außerdem bei einer Datenschutz-Aufsichtsbehörde beschweren (Art. 77 DSGVO).</p>
<h2>Pflicht zur Bereitstellung</h2><p>Die Website können Sie ohne Angaben nutzen. Für den Team-Login und Bewerbungen ist ein Discord-Konto erforderlich. Eine automatisierte Entscheidung im Sinne von Art. 22 DSGVO findet nicht statt; Eignungstests werden automatisch ausgewertet, über Bewerbungen entscheiden aber Menschen.</p>
</div>"""


STORAGE = (
    ("user_session", "Cookie", "Hält Teammitglieder angemeldet", "7 Tage"),
    ("oauth_state", "Cookie", "Schützt den Team-Login", "10 Minuten"),
    ("apply_session", "Cookie", "Hält Bewerbende im Portal angemeldet", "Sitzung im Portal"),
    ("apply_oauth_state und Rücksprung-Cookie", "Cookie", "Schützen den Bewerbungs-Login", "10 Minuten"),
    ("recovery_oauth_state", "Cookie", "Schützt die freiwillige Wiederherstellungs-Zustimmung", "10 Minuten"),
    ("Bewerbungsentwurf", "Local Storage", "Bewahrt Ihren Entwurf bis zum Absenden auf", "Bis zum Absenden oder Löschen"),
    ("Formularentwürfe, Scrollposition", "Session Storage", "Stellt das Dashboard nach dem Speichern wieder her", "Bis zum Schließen des Tabs"),
    ("theme, pulse.sidebar.collapsed", "Local Storage", "Merkt sich Darstellung und Seitenleiste im Dashboard", "Bis zum Löschen"),
)


def cookies_html(optional_services=()) -> str:
    rows = "".join(
        f"<tr><th scope=row>{e(name)}</th><td>{e(kind)}</td><td>{e(purpose)}</td><td>{e(period)}</td></tr>"
        for name, kind, purpose, period in STORAGE
    )
    if optional_services:
        choice = (
            "<h2>Ihre Auswahl</h2><p>Optionale Dienste laufen nur, wenn Sie zustimmen. Ihre Entscheidung können Sie hier jederzeit ändern.</p>"
            '<button class="btn btn-line" type="button" data-consent-open>Einstellungen ändern</button>'
        )
    else:
        choice = (
            "<h2>Einwilligung</h2><p>Diese Website setzt keine Analyse-, Marketing- oder Einbettungsdienste ein. "
            "Alle Einträge oben sind für die jeweilige Funktion unbedingt erforderlich (§ 25 Abs. 2 Nr. 2 TDDDG). "
            "Deshalb gibt es keinen Einwilligungsdialog: Es gibt nichts, dem Sie zustimmen müssten. "
            "Beim Besuch der öffentlichen Seiten wird nichts gespeichert.</p>"
        )
    return f"""<div class="doc"><h1>Cookies und Browser-Speicher</h1>
<p>Die Tabelle nennt alles, was Pulse in Ihrem Browser ablegt. Die öffentlichen Seiten selbst speichern nichts, die Einträge entstehen erst beim Login, im Bewerbungsportal oder im Dashboard.</p>
<div class="scroll"><table><thead><tr><th scope="col">Name</th><th scope="col">Art</th><th scope="col">Zweck</th><th scope="col">Dauer</th></tr></thead><tbody>{rows}</tbody></table></div>
{choice}
<p>Gespeicherte Daten können Sie jederzeit in Ihrem Browser löschen. Danach müssen Sie sich neu anmelden.</p>
</div>"""


def security_html() -> str:
    return """<div class="doc"><h1>Sicherheit</h1>
<p>So schützt Pulse Konten und Daten.</p>
<h2>Anmeldung</h2><p>Der Login läuft ausschließlich über Discord, Pulse speichert keine Passwörter. Sitzungen lassen sich widerrufen und enden, wenn jemand das Team verlässt.</p>
<h2>Rechte</h2><p>Jede interne Seite prüft die Berechtigung auf dem Server. Standard ist: kein Zugriff.</p>
<h2>Verbindung</h2><p>Im Produktivbetrieb ist die Verbindung per HTTPS verschlüsselt. Die öffentlichen Seiten laden keine Inhalte von fremden Servern.</p>
<h2>KI</h2><p>KI-Einschätzungen lösen nie allein eine Sanktion aus. Ein Mensch prüft und bestätigt jede Maßnahme.</p>
<h2>Sicherheitslücke melden</h2><p>Bitte melde Schwachstellen vertraulich über die Kontaktadresse im <a href="/impressum">Impressum</a> und nicht öffentlich im Discord.</p>
</div>"""
