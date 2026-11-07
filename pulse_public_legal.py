"""Impressum, privacy notice, cookie and security pages of the public website.

The texts describe what the code does (checked against the code on 2026-11-06). Operator details come
from the environment and are never invented: while a required value is missing, the page says so, and
the site stays out of search engines. docs/RECHTLICHE_FREIGABE.md lists what a person must still check.
"""
from __future__ import annotations

import os
from datetime import date
from html import escape
from urllib.parse import urlsplit

REQUIRED_FIELDS = {
    "PULSE_LEGAL_NAME": "Vollständiger bürgerlicher Vor- und Nachname des privaten Anbieters",
    "PULSE_LEGAL_ADDRESS": "Zustellfähige Anschrift des privaten Anbieters (Straße, Nr., PLZ, Ort; Zeilen mit | trennen)",
    "PULSE_LEGAL_EMAIL": "E-Mail-Adresse",
    "PULSE_LEGAL_HOSTER": "Name und Sitz des Hosting-Anbieters",
}
CONFIG_NOTICE = (
    '<p class="notice">Diese Seite ist noch nicht zur Veröffentlichung freigegeben. '
    'Es fehlen Betreiberangaben und/oder die dokumentierte Prüfung der Rechtstexte.</p>'
)


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def value(key: str) -> str:
    return os.getenv(key, "").strip()


def missing_fields() -> dict[str, str]:
    return {key: label for key, label in REQUIRED_FIELDS.items() if not value(key)}


def ready() -> bool:
    """Whether all four operator/provider fields are present (not a legal approval)."""
    return not missing_fields()


def _is_https_url(raw: str) -> bool:
    try:
        parsed = urlsplit(raw.strip())
        return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
    except ValueError:
        return False


def publication_blockers() -> dict[str, str]:
    """Require explicit operator review of the fact-dependent imprint regimes."""
    blockers = missing_fields()
    if value("PULSE_LEGAL_REVIEWED") != "1":
        blockers["PULSE_LEGAL_REVIEWED"] = "Rechts- und Datenschutztexte müssen vom Betreiber geprüft/freigegeben sein"
    if value("PULSE_TERMS_APPROVED") != "1":
        blockers["PULSE_TERMS_APPROVED"] = "Nutzungsbedingungen müssen ausdrücklich freigegeben sein"
    effective = value("PULSE_TERMS_EFFECTIVE_DATE")
    try:
        date.fromisoformat(effective)
    except ValueError:
        blockers["PULSE_TERMS_EFFECTIVE_DATE"] = "Gültigkeitsdatum der freigegebenen Nutzungsbedingungen (JJJJ-MM-DD)"

    ddg_assessment = value("PULSE_LEGAL_DDG_ASSESSMENT")
    if ddg_assessment not in {"applicable", "not_applicable"}:
        blockers["PULSE_LEGAL_DDG_ASSESSMENT"] = "Anwendbarkeit von § 5 DDG muss ausdrücklich geprüft werden"
    elif ddg_assessment == "applicable":
        contact_url = value("PULSE_LEGAL_CONTACT_METHOD_URL")
        if not value("PULSE_LEGAL_PHONE") and not _is_https_url(contact_url):
            blockers["PULSE_LEGAL_CONTACT_METHOD_URL"] = (
                "Bei Anwendbarkeit des § 5 DDG ist neben der E-Mail eine weitere tatsächlich funktionierende "
                "Möglichkeit zur schnellen elektronischen Kontaktaufnahme erforderlich"
            )

    mstv_assessment = value("PULSE_LEGAL_MSTV_18_2_ASSESSMENT")
    if mstv_assessment not in {"applicable", "not_applicable"}:
        blockers["PULSE_LEGAL_MSTV_18_2_ASSESSMENT"] = "Anwendbarkeit von § 18 Abs. 2 MStV (News-/Blog-Inhalte) muss geprüft werden"
    elif mstv_assessment == "applicable":
        if not value("PULSE_LEGAL_CONTENT_RESPONSIBLE_NAME"):
            blockers["PULSE_LEGAL_CONTENT_RESPONSIBLE_NAME"] = "Vor- und Nachname der redaktionell verantwortlichen Person"
        if not value("PULSE_LEGAL_CONTENT_RESPONSIBLE_ADDRESS"):
            blockers["PULSE_LEGAL_CONTENT_RESPONSIBLE_ADDRESS"] = "Zustellfähige Anschrift der redaktionell verantwortlichen Person"

    return blockers


def publication_ready() -> bool:
    """Only an explicit operator release unlocks indexing; presence of text alone is not approval."""
    return not publication_blockers()


def _or_missing(key: str) -> str:
    return e(value(key)) if value(key) else f'<span class="missing">{e(REQUIRED_FIELDS[key])} fehlt</span>'


def _address() -> str:
    raw = value("PULSE_LEGAL_ADDRESS")
    return "<br>".join(e(part.strip()) for part in raw.split("|")) if raw else _or_missing("PULSE_LEGAL_ADDRESS")


def _operator() -> str:
    email = value("PULSE_LEGAL_EMAIL")
    mail = f'<a href="mailto:{e(email)}">{e(email)}</a>' if email else _or_missing("PULSE_LEGAL_EMAIL")
    contact = ""
    phone = value("PULSE_LEGAL_PHONE")
    contact_url = value("PULSE_LEGAL_CONTACT_METHOD_URL")
    contact_label = value("PULSE_LEGAL_CONTACT_METHOD_LABEL") or "Weitere Kontaktmöglichkeit"
    if phone:
        contact = f"<br>Telefon: {e(phone)}"
    elif _is_https_url(contact_url):
        contact = f'<br>{e(contact_label)}: <a href="{e(contact_url)}" rel="noopener noreferrer">{e(contact_url)}</a>'
    return f"<p>{_or_missing('PULSE_LEGAL_NAME')}<br>{_address()}</p><p>E-Mail: {mail}{contact}</p>"


def _optional_legal_details() -> str:
    """Render only optional legal details actually supplied by the operator."""
    fields = (
        ("PULSE_LEGAL_FORM", "Rechtsform"),
        ("PULSE_LEGAL_REPRESENTATIVE", "Vertretungsberechtigte Person"),
        ("PULSE_LEGAL_REGISTER", "Registergericht und Registernummer"),
        ("PULSE_LEGAL_VAT_ID", "Umsatzsteuer-Identifikationsnummer"),
        ("PULSE_LEGAL_WIDNR", "Wirtschafts-Identifikationsnummer"),
        ("PULSE_LEGAL_AUTHORITY", "Zuständige Aufsichtsbehörde"),
        ("PULSE_LEGAL_PROFESSION", "Berufsrechtliche Angaben"),
    )
    rows = "".join(
        f"<dt>{e(label)}</dt><dd>{e(value(key))}</dd>"
        for key, label in fields if value(key)
    )
    extra = value("PULSE_LEGAL_EXTRA")
    if extra:
        rows += f"<dt>Weitere Angaben</dt><dd>{e(extra)}</dd>"
    return f"<h2>Weitere gesetzliche Angaben</h2><dl>{rows}</dl>" if rows else ""


def _privacy_controller() -> str:
    """Use explicit controller overrides when the legal website operator is not the controller."""
    name = value("PULSE_PRIVACY_CONTROLLER_NAME") or value("PULSE_LEGAL_NAME")
    address = value("PULSE_PRIVACY_CONTROLLER_ADDRESS") or value("PULSE_LEGAL_ADDRESS")
    email = value("PULSE_PRIVACY_CONTROLLER_EMAIL") or value("PULSE_LEGAL_EMAIL")
    address_html = "<br>".join(e(part.strip()) for part in address.split("|") if part.strip()) if address else _or_missing("PULSE_LEGAL_ADDRESS")
    name_html = e(name) if name else _or_missing("PULSE_LEGAL_NAME")
    email_html = f'<a href="mailto:{e(email)}">{e(email)}</a>' if email else _or_missing("PULSE_LEGAL_EMAIL")
    phone = f"<br>Telefon: {e(value('PULSE_LEGAL_PHONE'))}" if value("PULSE_LEGAL_PHONE") else ""
    return f"<p>{name_html}<br>{address_html}</p><p>E-Mail: {email_html}{phone}</p>"




def impressum_html() -> str:
    responsible_html = ""
    if value("PULSE_LEGAL_MSTV_18_2_ASSESSMENT") == "applicable":
        responsible_name = value("PULSE_LEGAL_CONTENT_RESPONSIBLE_NAME")
        responsible_address = value("PULSE_LEGAL_CONTENT_RESPONSIBLE_ADDRESS")
        address_html = "<br>".join(e(part.strip()) for part in responsible_address.split("|") if part.strip())
        responsible_html = (
            "<h2>Verantwortlich für journalistisch-redaktionelle Inhalte (§ 18 Abs. 2 MStV)</h2>"
            f"<p>{e(responsible_name)}<br>{address_html}</p>"
        )
    elif value("PULSE_LEGAL_MSTV_18_2_ASSESSMENT") == "not_applicable":
        responsible_html = (
            "<p class=\"notice\">Die Anwendbarkeit des § 18 Abs. 2 MStV für den News-/Blog-Bereich "
            "wurde vom Betreiber gesondert geprüft und als nicht einschlägig bewertet.</p>"
        )
    else:
        responsible_html = (
            "<p class=\"notice\">Die Anwendbarkeit des § 18 Abs. 2 MStV für den News-/Blog-Bereich "
            "muss vor Freigabe geprüft werden.</p>"
        )
    return f"""<div class="doc"><h1>Impressum</h1>
{"" if publication_ready() else CONFIG_NOTICE}
<h2>Anbieterkennzeichnung</h2>
<p>Für Telemedien, die nicht ausschließlich persönlichen oder familiären Zwecken dienen, verlangt § 18 Abs. 1 MStV leicht erkennbare, unmittelbar erreichbare und ständig verfügbare Angaben zu Name und Anschrift des Anbieters. BOCHUM RP wird nach Betreiberangabe von einer natürlichen Person betrieben; daher ist der vollständige bürgerliche Vor- und Nachname dieser Person anzugeben. Die zusätzlichen Vertreterangaben für juristische Personen sind hier nicht einschlägig. Ob § 5 DDG zusätzlich gilt, hängt davon ab, ob der digitale Dienst geschäftsmäßig und in der Regel gegen Entgelt angeboten wird. Bei Anwendbarkeit sind neben der E-Mail-Adresse weitere Angaben für eine schnelle elektronische Kontaktaufnahme und unmittelbare Kommunikation erforderlich.</p>
{_operator()}
{_optional_legal_details()}
{responsible_html}
<h2>Kontakt und externe Plattformen</h2>
<p>BOCHUM RP ist eine Roleplay-Community. Rollenspielhandlungen und dargestellte Ereignisse sind fiktiv. Verlinkte Dienste wie Discord oder Roblox werden von eigenständigen Anbietern betrieben; für deren Plattformen gelten die jeweiligen Bedingungen und Datenschutzhinweise.</p>
<h2>Hinweise zu Inhalten und Rechten</h2>
<p>Öffentliche Inhalte werden nach bestem Wissen gepflegt. Hinweise auf mögliche Rechtsverletzungen können an die oben genannte Kontaktadresse gesendet werden. Bitte benennen Sie den konkreten Inhalt und die betroffene URL, damit der Hinweis geprüft werden kann. Eine pauschale Übernahme fremder Inhalte oder Rechte wird nicht erklärt.</p>
</div>"""



SECTIONS = (
    ("Website aufrufen", "Beim Aufruf verarbeitet der Server technisch Ihre IP-Adresse, den Zeitpunkt, die aufgerufene Seite und die Browserkennung, damit die Seite ausgeliefert und vor Überlastung geschützt werden kann. Zur Begrenzung von Anfragen hält der Server die Adresse kurzzeitig im Arbeitsspeicher.",
     "Art. 6 Abs. 1 lit. f DSGVO (sicherer Betrieb).", "Hosting-Anbieter.",
     "Begrenzungsdaten im Arbeitsspeicher nur für das Zeitfenster. Technische Protokolle rotieren (vier Dateien mit je 1 MB)."),
    ("Team-Login mit Discord", "Der Login nutzt Discord OAuth mit dem Bereich „identify“. Pulse erhält Ihre Discord-ID, Ihren Namen und Ihr Avatar. Für jede Anmeldung werden IP-Adresse, Zeitpunkt und Browserkennung protokolliert, um Konten zu schützen. Nur Administratoren sehen diese Einträge.",
     "Art. 6 Abs. 1 lit. f DSGVO (Kontosicherheit), bei Teammitgliedern zusätzlich Art. 6 Abs. 1 lit. b DSGVO.", "Discord als Anmeldedienst, Hosting-Anbieter.",
     "Anmeldeprotokoll 30 Tage. Sitzungen enden nach 7 Tagen oder nach 24 Stunden ohne Aktivität."),
    ("Bewerbungen", "Das Portal verarbeitet Ihre Discord-Identität, Ihre Antworten, Testergebnisse, Praxisaufgaben sowie den Bearbeitungsstand. Ein nicht abgesendeter Entwurf bleibt in Ihrem Browser, bis Sie ihn absenden oder selbst löschen.",
     "Art. 6 Abs. 1 lit. b DSGVO, soweit die Bewerbung auf eine konkrete Aufnahme oder Funktion gerichtet ist; andernfalls muss der Betreiber die passende Rechtsgrundlage festlegen.", "Berechtigte Teammitglieder, Discord für Benachrichtigungen und der Hosting-Anbieter.",
     "Eine automatische Löschung läuft aktuell nicht: Die Retention-Funktion ist technisch fail-closed/deaktiviert. Daten bleiben bis zur tatsächlichen manuellen Löschung gespeichert; eine geplante Frist ist kein Nachweis einer erfolgten Löschung."),
    ("Team-Dashboard", "Für Teammitglieder verarbeitet Pulse je nach genutzter Funktion Dienstzeiten, Aufgaben, Abwesenheiten, Bewerbungs- und Beschäftigungsbezüge, Verwarnungen, Notizen und Aktionsprotokolle. Zugriffe sind rollen- und berechtigungsabhängig.",
     "Je nach Einzelfunktion kommen Art. 6 Abs. 1 lit. b DSGVO (Durchführung einer Vereinbarung) oder lit. f DSGVO (Organisation, Sicherheit und nachvollziehbare Moderation) in Betracht. Die Interessenabwägung und die konkrete Zuordnung sind vom Betreiber zu dokumentieren.", "Berechtigte Teammitglieder und Hosting-Anbieter; interne Zugriffe richten sich nach den Pulse-Berechtigungen.",
     "Es besteht derzeit keine einheitlich technisch durchgesetzte Löschfrist für alle Teamdaten. Die zentrale Retention-Bereinigung ist deaktiviert; einzelne Datenbestände werden nach den jeweiligen Abläufen manuell verwaltet. Backups können gelöschte Daten bis zum Ablauf ihrer eigenen Rotation weiter enthalten."),
    ("Öffentliche Teamprofile", "Die öffentliche Teamseite zeigt ausschließlich Mitglieder der konfigurierten TeamBot-Rollen, die zuvor über den Discord-Befehl /teamseite_freigabe nach Anzeige eines eigenen Hinweises ausdrücklich zugestimmt und die Bestätigungsschaltfläche betätigt haben. Pulse speichert dafür die Discord-ID, den Zeitpunkt und die Textversion der Freigabe sowie die betroffenen Teamrollen-IDs in der Pulse-Datenbank. Die Website gibt nur Discord-Anzeigename, aktuelle Teamrolle und – sofern vorhanden – Profilbild aus. Präsenzstatus, Abwesenheiten, private Kontaktdaten, Verwarnungen und interne Notizen bleiben verborgen. Eine Änderung der konfigurierten Teamrollen erfordert eine erneute Freigabe. Der Widerruf erfolgt über /teamseite_freigabe_widerrufen und beendet die Anzeige bei der nächsten Seitenanfrage.",
     "Art. 6 Abs. 1 lit. a DSGVO (ausdrückliche, freiwillige Einwilligung). Die Bestätigung ist optional und hat keine Auswirkung auf Teamrolle, Teammitgliedschaft oder interne Berechtigungen. Der Widerruf ist jederzeit möglich und berührt nicht die Rechtmäßigkeit der Verarbeitung bis zum Widerruf.",
     "Besucher der öffentlichen Website, Hosting-Anbieter, Discord beim Abruf von Profilbildern über seine CDN-Domains sowie die Pulse-Datenbank zur Speicherung des Freigabenachweises. CDN-Bildadressen können technische Discord-Kennungen enthalten. Die Discord-ID ist kein sichtbares Profilfeld. Die Freigabe wird nur der betroffenen Person über eine ephemere Discord-Nachricht angeboten.",
     "Die öffentliche Anzeige endet mit dem Widerruf, wenn sich der konfigurierte Teamrollen-Scope ändert oder wenn das Mitglied den Server verlässt. Der minimale Freigabenachweis (Discord-ID, Textversion, Teamrollen-Scope, Erteilungs- und ggf. Widerrufszeitpunkt) bleibt in der Datenbank erhalten; eine verbindliche Löschfrist für diesen Nachweis muss der Verantwortliche noch festlegen und technisch umsetzen. Profilbilder werden beim Seitenaufruf vom Discord-CDN angefordert; dort gelten die Datenschutzinformationen von Discord."),
    ("Discord-Server und Bot", "Der Bot verarbeitet je nach Funktion Discord-Nachrichten und Nachrichtenbezüge, Ticket-Inhalte und Transkripte, Rollen- und Mitgliedschaftsänderungen sowie Sprachkanal-Ereignisse. Inhalte können in Ticket-Transkripten oder internen Fallakten gespeichert werden.",
     "Je nach Vorgang Art. 6 Abs. 1 lit. f DSGVO (Betrieb, Sicherheit und Moderation der Community) oder eine andere vom Betreiber zu dokumentierende Rechtsgrundlage.", "Discord als Plattformanbieter, berechtigte Teammitglieder und Hosting-Anbieter.",
     "Ticket-Transkripte haben derzeit keine automatische Löschfrist und bleiben bis zur manuellen Löschung gespeichert. Entschiedene KI-Moderationsfälle werden nach dem aktuellen Code nach 30 Tagen bereinigt; offene Fälle können länger bestehen bleiben."),
    ("KI-Unterstützung", "Nur wenn ein KI-Anbieter konfiguriert und die jeweilige Funktion aktiviert ist, übermittelt Pulse Daten an Google Gemini oder OpenAI. Beim Support-Assistenten werden die aktuelle Frage und bis zu acht vorherige Gesprächsnachrichten aus dem flüchtigen Pulse-Verlauf übertragen. Bei der KI-Moderation können Nachrichtentext, Discord-Nutzerkennung und -Name, Kanal, Rollen, Erwähnungen, Zeitstempel und technische Fallmerkmale übermittelt werden. Die KI entscheidet nicht allein über Sanktionen; eine zuständige Person prüft die Maßnahme. Der Befehl /ki_vergessen entfernt den in Pulse gespeicherten Gesprächsverlauf, kann aber bereits übermittelte Daten oder Anbieterprotokolle nicht zurückholen.",
     "Die konkrete Rechtsgrundlage und die Interessenabwägung sind für Support und Moderation getrennt festzulegen. Art. 6 Abs. 1 lit. f DSGVO kommt nur bei dokumentierter Interessenabwägung in Betracht.", "Der jeweils konfigurierte KI-Anbieter (Google Gemini oder OpenAI), Discord und berechtigte Teammitglieder. Der genaue Anbieter und die Region hängen von der aktiven Konfiguration und dem gebuchten Tarif ab.",
     "Support-Verlauf nur im Arbeitsspeicher bis zum Neustart, zur automatischen Begrenzung oder /ki_vergessen. Entschiedene KI-Moderationsfälle nach 30 Tagen; offene Fälle bis zur Entscheidung. Speicher- und Trainingsregeln beim KI-Anbieter hängen vom konkret genutzten Konto, Tarif und Vertrag ab und müssen separat geprüft werden."),
    ("Roblox-Abfragen", "Das Dashboard fragt zur Zuordnung von Teammitgliedern und Moderationsfällen öffentliche Roblox-Profile über die Roblox-Schnittstelle ab.",
     "Art. 6 Abs. 1 lit. f DSGVO.", "Roblox.", "Im Arbeitsspeicher zwischengespeichert, Anfragen werden nicht dauerhaft abgelegt."),
    ("Backups", "Automatische Komplett-Sicherungen enthalten die oben genannten Daten, damit sie nach einem Ausfall wiederhergestellt werden können.",
     "Art. 6 Abs. 1 lit. f DSGVO (Datensicherheit).", "Hosting-Anbieter.",
     "Rollierend: täglich für 7 Tage, wöchentlich für 4 Wochen, monatlich für 6 Monate."),
)


def privacy_html() -> str:
    rows = "".join(
        f"<section><h3>{e(title)}</h3><p>{e(what)}</p><dl><dt>Rechtsgrundlage</dt><dd>{e(basis)}</dd>"
        f"<dt>Empfänger und Zugriffe</dt><dd>{e(recipients)}</dd><dt>Speicherdauer</dt><dd>{e(period)}</dd></dl></section>"
        for title, what, basis, recipients, period in SECTIONS
    )
    dpo = value("PULSE_PRIVACY_DPO_CONTACT")
    authority = value("PULSE_PRIVACY_SUPERVISORY_AUTHORITY")
    transfer = value("PULSE_PRIVACY_TRANSFER_INFO")
    dpo_html = f"<p>Datenschutzbeauftragte/r: {e(dpo)}</p>" if dpo else (
        "<p>Ein Datenschutzbeauftragter ist nur zu benennen, wenn die gesetzlichen Voraussetzungen erfüllt sind. "
        "Ob dies hier der Fall ist, muss der Verantwortliche prüfen.</p>"
    )
    authority_html = f"Zuständige Datenschutzaufsichtsbehörde: {e(authority)}" if authority else (
        "Die zuständige Datenschutzaufsichtsbehörde richtet sich nach der Niederlassung des Verantwortlichen "
        "und den gesetzlichen Zuständigkeitsregeln. Bitte wenden Sie sich an die für den Verantwortlichen zuständige Behörde."
    )
    transfer_html = e(transfer) if transfer else (
        "Bei Discord und bei aktivierten KI-Diensten kann eine Verarbeitung außerhalb des Europäischen Wirtschaftsraums "
        "stattfinden. Der konkrete Zielstaat, die Rolle des jeweiligen Anbieters und der tatsächlich genutzte "
        "Übermittlungsmechanismus (z. B. Angemessenheitsbeschluss oder geeignete Garantien) müssen vor Freigabe anhand "
        "des konkreten Kontos, Tarifs, Vertrags und der Unterauftragnehmer geprüft und hier ergänzt werden. "
        "Diese Seite behauptet nicht, dass ein bestimmter Mechanismus bereits geprüft wurde."
    )
    return f"""<div class="doc"><h1>Datenschutzerklärung</h1>
{"" if publication_ready() else CONFIG_NOTICE}
<p>Diese Erklärung beschreibt die Verarbeitung personenbezogener Daten durch BOCHUM RP und Pulse. Sie nennt Zwecke, Rechtsgrundlagen, Empfänger und Speicherpraxis nach dem derzeit geprüften Softwarestand. Die konkrete rechtliche Einordnung und alle Anbieter-Verträge müssen vom Verantwortlichen vor einer endgültigen Freigabe geprüft werden.</p>
<h2>Verantwortlicher</h2>
{_privacy_controller()}
{dpo_html}
<h2>Hosting und technische Dienstleister</h2>
<p>Die Website und der Bot laufen bei: {_or_missing("PULSE_LEGAL_HOSTER")}. Der Hosting-Anbieter erhält technisch erforderliche Verbindungs- und Systemdaten. Ob und in welchem Umfang er als Auftragsverarbeiter nach Art. 28 DSGVO tätig ist, richtet sich nach dem tatsächlich geschlossenen Vertrag und den eingesetzten Unterauftragnehmern; dies muss der Verantwortliche anhand der Vertragsunterlagen prüfen.</p>
<h2>Verarbeitungen im Einzelnen</h2>{rows}
<h2>Daten aus anderen Quellen (Art. 14 DSGVO)</h2>
<p>Bei der öffentlichen Teamvorstellung erhält Pulse Teamrollen und Profilinformationen über den Discord-Server bzw. dessen Mitglieds- und Rollenobjekte. Die betroffene Person stellt diese Daten nicht bei jedem Abruf der Website erneut selbst bereit. Die öffentliche Darstellung ist auf Anzeigename, Teamrolle und – soweit vorhanden – Profilbild beschränkt. Rollen-/Profiländerungen werden aus dem aktuellen Discord-Zustand übernommen. Die Information der betroffenen Teammitglieder, die Rechtsgrundlage und der organisatorische Ablauf für Einwände oder Löschungswünsche müssen zusätzlich im Aufnahmeprozess und in der Praxis sichergestellt werden.</p>
<h2>Empfänger und externe Plattformen</h2>
<p>Je nach Funktion können Discord, Roblox, der Hosting-Anbieter, berechtigte Teammitglieder und – nur bei aktivierter KI-Funktion – der ausgewählte KI-Anbieter Daten erhalten. Discord und Roblox stellen eigenständige Plattformdienste bereit. Die rechtliche Rolle jedes Empfängers (eigener Verantwortlicher, gemeinsam Verantwortlicher oder Auftragsverarbeiter) muss anhand der konkreten Funktion und Verträge geprüft werden; die bloße Nennung eines Anbieters legt diese Rolle nicht fest.</p>
<h2>Übermittlung außerhalb des EWR</h2><p>{transfer_html}</p>
<h2>Cookies und Browser-Speicher</h2>
<p>Die Übersicht unter <a href="/cookies">Cookies und Browser-Speicher</a> nennt die von Pulse verwendeten Cookies, Local-Storage- und Session-Storage-Einträge. Die öffentlichen Inhaltsseiten speichern beim normalen Seitenaufruf keine dauerhaften Browserprofile. Die rechtliche Einordnung jeder einzelnen Speicherung bzw. jedes Zugriffs nach § 25 TDDDG ist unabhängig davon zu prüfen, ob eine Speicherung technisch für eine Funktion verwendet wird.</p>
<h2>Ihre Datenschutzrechte</h2>
<p>Sie können – soweit die gesetzlichen Voraussetzungen erfüllt sind – Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung (Art. 18), Datenübertragbarkeit (Art. 20) sowie Widerspruch gegen Verarbeitungen auf Grundlage berechtigter Interessen (Art. 21) verlangen. Eine Einwilligung kann jederzeit mit Wirkung für die Zukunft widerrufen werden (Art. 7 Abs. 3). Die Rechtmäßigkeit der Verarbeitung bis zum Widerruf bleibt davon unberührt. Für Anliegen genügt eine Nachricht an die oben angegebene Kontaktadresse; bitte beschreiben Sie möglichst konkret, welche Funktion oder Daten betroffen sind.</p>
<p>Sie können außerdem eine Beschwerde bei der zuständigen Datenschutzaufsichtsbehörde einreichen (Art. 77 DSGVO). {authority_html}</p>
<h2>Pflicht zur Bereitstellung und Folgen</h2>
<p>Die öffentlichen Informationsseiten können grundsätzlich ohne ein Nutzerkonto aufgerufen werden. Für Team-Login und Bewerbungen ist ein Discord-Konto erforderlich; ohne die erforderlichen Angaben können die jeweilige Anmeldung oder Bewerbung nicht durchgeführt werden. Welche Angaben zwingend sind, ergibt sich aus dem jeweiligen Formular und dem konkreten Vorgang.</p>
<h2>Automatisierte Entscheidungen</h2>
<p>Pulse nutzt automatisierte Auswertungen bei Eignungstests und KI-gestützte Hinweise bei der Moderation. Nach dem derzeitigen Ablauf trifft die Software keine allein verbindliche Entscheidung über die Aufnahme oder eine Sanktion; Teammitglieder prüfen und entscheiden. Sollte sich dieser Ablauf ändern, muss die Erklärung vorab angepasst werden.</p>
<h2>Speicherfristen und Löschung</h2>
<p>Die oben genannten Fristen beschreiben den aktuellen technischen Stand, keine Garantie einer bereits erfolgten Löschung. Die zentrale Datenbereinigung ist derzeit fail-closed/deaktiviert; insbesondere werden Bewerbungs- und allgemeine Auditdaten nicht automatisch nach den konfigurierten Ziel-Fristen gelöscht. Ticket-Transkripte besitzen keine automatische Löschfrist. Backups können Daten bis zum Ablauf der Backup-Rotation weiter enthalten. Der Betreiber muss die offenen Fristen organisatorisch festlegen und eine tatsächliche Löschung einschließlich Sicherungskopien umsetzen.</p>
<h2>Änderungen</h2>
<p>Diese Erklärung wird angepasst, wenn sich Datenverarbeitungen, Anbieter, technische Abläufe oder rechtliche Anforderungen ändern. Maßgeblich ist die jeweils auf dieser Website veröffentlichte Fassung.</p>
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
            "<h2>Optionale Dienste und Einwilligung</h2><p>Derzeit sind auf den öffentlichen Inhaltsseiten keine Analyse-, Marketing- oder optionalen Einbettungsdienste aktiviert. "
            "Ein Einwilligungsdialog wird deshalb nicht für nicht vorhandene optionale Dienste eingeblendet. "
            "Ob einzelne Cookies oder Browser-Speicher-Einträge unter die Ausnahme des § 25 Abs. 2 TDDDG fallen, "
            "muss für die jeweilige Funktion gesondert anhand ihrer objektiven Erforderlichkeit geprüft werden. "
            "Beim normalen Aufruf öffentlicher Inhaltsseiten wird kein dauerhaftes Browserprofil angelegt.</p>"
        )
    return f"""<div class="doc"><h1>Cookies und Browser-Speicher</h1>
<p>Die Tabelle nennt alles, was Pulse in Ihrem Browser ablegt. Die öffentlichen Seiten selbst speichern nichts, die Einträge entstehen erst beim Login, im Bewerbungsportal oder im Dashboard.</p>
<div class="scroll"><table><thead><tr><th scope="col">Name</th><th scope="col">Art</th><th scope="col">Zweck</th><th scope="col">Dauer</th></tr></thead><tbody>{rows}</tbody></table></div>
{choice}
<p>Gespeicherte Daten können Sie jederzeit in Ihrem Browser löschen. Danach müssen Sie sich neu anmelden.</p>
</div>"""



def terms_html() -> str:
    """Render community terms as a draft until the operator has reviewed and approved them."""
    if not publication_ready():
        return """<div class="doc"><h1>Nutzungsbedingungen</h1>
<p class="notice">Entwurf – noch nicht freigegeben. Dieser Text ist nicht als abschließend geprüfte Vertragsfassung freigegeben.</p>
<p>Die bloße Veröffentlichung einer Textseite belegt noch nicht, dass Allgemeine Geschäftsbedingungen wirksam in einen Vertrag einbezogen wurden. Soll dieser Text Vertragsbestandteil werden, müssen Nutzer beim jeweiligen Vertragsschluss deutlich darauf hingewiesen werden, die Bedingungen zumutbar einsehen können und ihrer Geltung zustimmen (§ 305 Abs. 2 BGB). Ein entsprechender Ablauf muss vor dem Einsatz als AGB technisch und rechtlich geprüft werden.</p>
<h2>Vorgesehene Regelungsbereiche</h2>
<ul>
<li>Geltungsbereich und Verhältnis zum BOCHUM-RP-Regelwerk</li>
<li>Konten, Discord-/Roblox-Plattformen und zulässige Nutzung</li>
<li>Verhalten, Schutz anderer Nutzer und Verbot von Manipulationen</li>
<li>Inhalte, Urheberrechte und Rechte Dritter</li>
<li>Bewerbungen, Teammitgliedschaft, Moderation und Überprüfung von Maßnahmen</li>
<li>KI-Unterstützung, Verfügbarkeit, Änderungen und Haftung nach Gesetz</li>
<li>Datenschutz, Kontakt und Meldung von Rechtsverletzungen</li>
</ul>
<p><a href="/regelwerk">Zum aktuellen Community-Regelwerk</a></p></div>"""
    effective = date.fromisoformat(value("PULSE_TERMS_EFFECTIVE_DATE")).strftime("%d.%m.%Y")
    return f"""<div class="doc"><h1>Nutzungsbedingungen</h1>
<p>Stand: {e(effective)}. Diese Bedingungen beschreiben die Nutzung der öffentlichen Website und der Community-Angebote von BOCHUM RP. Das jeweils veröffentlichte <a href="/regelwerk">Community-Regelwerk</a> ergänzt diese Bedingungen für das Roleplay und den Discord-Server. Für externe Plattformen gelten zusätzlich deren eigene Vertrags- und Nutzungsbedingungen.</p>
<p class="notice">Die Veröffentlichung dieser Seite allein beweist keine wirksame Einbeziehung in einen Vertrag. Soweit diese Bedingungen als AGB verwendet werden sollen, müssen Hinweis, zumutbare Kenntnisnahmemöglichkeit und Einverständnis beim jeweiligen Vertragsschluss nach § 305 Abs. 2 BGB sichergestellt werden. Die rechtliche Wirksamkeit jeder Klausel richtet sich insbesondere nach §§ 305 ff. BGB.</p>
<h2>1. Geltungsbereich</h2><p>Diese Bedingungen gelten für die Nutzung der von BOCHUM RP bereitgestellten Website und Community-Funktionen, soweit beim jeweiligen Angebot keine spezielleren Bedingungen angezeigt werden. Zwingende gesetzliche Rechte bleiben unberührt.</p>
<h2>2. Konten und Zugang</h2><p>Für bestimmte Funktionen ist eine Anmeldung über Discord erforderlich. Nutzer müssen Zugangsdaten und Konten schützen und dürfen keine fremden Identitäten verwenden. Pulse speichert keine Discord-Passwörter. Die Verfügbarkeit und Sicherheit des Discord-Kontos selbst liegen zusätzlich im Verantwortungsbereich des jeweiligen Plattformanbieters.</p>
<h2>3. Zulässige Nutzung und Verhalten</h2><p>Nutzer müssen Gesetze, Rechte anderer, Plattformbedingungen und das aktuelle BOCHUM-RP-Regelwerk beachten. Untersagt sind insbesondere Belästigung, Drohungen, diskriminierende oder rechtswidrige Inhalte, Betrugsversuche, Identitätsmissbrauch, Manipulation von Bewerbungen, Umgehung von Berechtigungen, Angriffe auf Systeme und absichtliche Störungen des Betriebs.</p>
<h2>4. Inhalte und Rechte</h2><p>Nutzer dürfen nur Inhalte übermitteln, an denen sie die erforderlichen Rechte besitzen und deren Verarbeitung bzw. Veröffentlichung zulässig ist. Rechte Dritter, insbesondere Urheber-, Marken-, Persönlichkeits- und Datenschutzrechte, sind zu beachten. Mit dem Übermitteln erhält BOCHUM RP nur diejenige einfache, auf die jeweilige Funktion beschränkte Nutzungserlaubnis, die zur Entgegennahme, Speicherung, Prüfung und – bei einer ausdrücklich gewählten Veröffentlichungsfunktion – Anzeige des Inhalts erforderlich ist. Eine darüber hinausgehende Rechteübertragung wird mit diesen Bedingungen nicht erklärt.</p>
<h2>5. Bewerbungen und Teammitgliedschaft</h2><p>Bewerbungen müssen wahrheitsgemäß und vollständig sein. Eine Bewerbung begründet keinen Anspruch auf Aufnahme, eine bestimmte Teamrolle oder eine spätere Beförderung. Teamfunktionen und interne Berechtigungen richten sich nach dem aktuellen Regelwerk und den Entscheidungen der zuständigen Leitung. Die Beendigung einer Teamfunktion hebt gesetzliche Ansprüche und Datenschutzrechte nicht auf.</p>
<h2>6. Moderation und Maßnahmen</h2><p>BOCHUM RP kann gemeldete Inhalte und Vorgänge prüfen und bei Regelverstößen angemessene Maßnahmen nach dem veröffentlichten Regelwerk treffen. Soweit eine menschliche Entscheidung vorgesehen ist, dienen automatisierte oder KI-gestützte Systeme nur als Unterstützung. Betroffene können eine Überprüfung über die im Impressum angegebene Kontaktmöglichkeit oder den verfügbaren Support anfragen. Gesetzliche Rechte bleiben unberührt.</p>
<h2>7. KI-Funktionen</h2><p>Einzelne Support- oder Moderationsfunktionen können externe KI-Dienste verwenden. KI-Ausgaben können fehlerhaft oder unvollständig sein und sind keine verbindlichen Rechts-, Sicherheits- oder Verwaltungsentscheidungen. Verbindliche Community-Maßnahmen werden durch die zuständigen Teammitglieder getroffen.</p>
<h2>8. Verfügbarkeit und Änderungen</h2><p>Die Community-Angebote werden ohne Zusage einer ununterbrochenen Verfügbarkeit bereitgestellt. Wartung, Sicherheitsvorfälle, Störungen externer Plattformen oder technische Gründe können zu Einschränkungen führen. Änderungen der Angebote und dieser Bedingungen werden mit einem aktualisierten Stand kenntlich gemacht; gesetzliche Anforderungen an Änderungen und bestehende Rechte bleiben unberührt.</p>
<h2>9. Externe Plattformen</h2><p>Discord, Roblox und weitere verlinkte Dienste werden von Dritten betrieben. Für deren Verfügbarkeit, Konten, Inhalte und Datenverarbeitung gelten die Bedingungen und Datenschutzhinweise der jeweiligen Anbieter. BOCHUM RP kann deren Dienste nicht kontrollieren.</p>
<h2>10. Haftung</h2><p>Es gelten die gesetzlichen Haftungsregelungen. Diese Bedingungen schließen keine Haftung aus, soweit ein Ausschluss oder eine Begrenzung gesetzlich unzulässig ist; insbesondere bleiben zwingende Haftungstatbestände unberührt.</p>
<h2>11. Datenschutz</h2><p>Informationen zu Art, Umfang und Zweck der Verarbeitung personenbezogener Daten stehen in der <a href="/datenschutz">Datenschutzerklärung</a>. Datenschutzrechte bestehen unabhängig von diesen Nutzungsbedingungen.</p>
<h2>12. Kontakt und Meldungen</h2><p>Fragen, Beschwerden und Hinweise auf mögliche Rechtsverletzungen können über die im <a href="/impressum">Impressum</a> angegebene Kontaktmöglichkeit eingereicht werden. Bitte nennen Sie den betroffenen Inhalt oder Vorgang und fügen Sie, soweit möglich, die konkrete URL oder den Discord-Kontext hinzu.</p>
</div>"""



def security_html() -> str:
    return """<div class="doc"><h1>Sicherheit</h1>
<p>So schützt Pulse Konten und Daten.</p>
<h2>Anmeldung</h2><p>Der Login läuft ausschließlich über Discord, Pulse speichert keine Passwörter. Sitzungen lassen sich widerrufen und enden, wenn jemand das Team verlässt.</p>
<h2>Rechte</h2><p>Jede interne Seite prüft die Berechtigung auf dem Server. Standard ist: kein Zugriff.</p>
<h2>Verbindung</h2><p>Im Produktivbetrieb ist die Verbindung per HTTPS verschlüsselt. Die öffentliche Teamvorstellung kann Profilbilder direkt von den ausdrücklich zugelassenen Discord-CDN-Domains laden; andere öffentliche Inhaltsseiten binden derzeit keine externen Medien ein.</p>
<h2>KI</h2><p>KI-Einschätzungen lösen nie allein eine Sanktion aus. Ein Mensch prüft und bestätigt jede Maßnahme.</p>
<h2>Sicherheitslücke melden</h2><p>Bitte melde Schwachstellen vertraulich über die Kontaktadresse im <a href="/impressum">Impressum</a> und nicht öffentlich im Discord.</p>
</div>"""
