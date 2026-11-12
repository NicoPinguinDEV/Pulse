# Pulse – Statusaudit der 177 Funktionsideen

**Ursprünglicher Auditstand:** `main` bei `c564dc448e647b48a649659da8c2c8b0a834301d` (Kurz-SHA `c564dc4`)  
**Aktueller Abgleich:** `main` @ `75555c79f942de700ad4a48c53d43fa8fce591b5` (PRs #214–#219 sowie das Notfallhandbuch #164 sind gemergt). Der Auditstand wurde nach den jüngsten Merges anhand der geänderten Module und ihrer Verhaltenstests aktualisiert: **78 fertig / 99 teilweise / 0 offen**. #10 besitzt inzwischen eine getestete Deutsch-/Englisch-Locale-Grundlage, ist aber wegen der noch nicht durchgängigen Übersetzung teilweise. #25, #45, #46 und #49 sind nach den gemergten, getesteten Discord-/Dashboard-Flows fertig. Fortschritt und Nachweise: `docs/PULSE_177_IMPLEMENTATION_PROGRESS.md`.  
**Produktversion:** `pulse_version.py` / README führen Pulse 7.17.0  
**Umfang:** Die 177 nummerierten Ideen aus der Gesprächsliste – Discord-Bot, Dashboard, gemeinsame Plattform, Intelligence, Workflows, Betrieb und BOCHUM-RP-Leitstelle.

> Dieses Dokument ist eine Bestandsaufnahme des Repository-Codes und der vorhandenen Tests. Es ist keine Aussage, dass alle Abläufe auf dem Live-Discord-Server manuell abgenommen wurden. Die Bewertung bezieht sich auf den Code in `main`, nicht auf den derzeit auf dem VPS laufenden Stand. Der VPS wurde durch dieses Audit nicht verändert.

## Bewertungsmaßstab

- **Fertig:** Die beschriebene Kernfunktion ist im aktuellen Code vorhanden und es gibt passende Verhaltenstests oder eine eindeutige, getestete End-to-End-Abdeckung.
- **Teilweise:** Ein passender Baustein existiert, aber mindestens ein wesentlicher Teil der beschriebenen Anforderung fehlt, ist nur in einer Oberfläche verfügbar oder nicht ausreichend durch Verhaltenstests abgesichert.
- **Offen:** Im aktuellen Repository wurde kein passender implementierter Ablauf gefunden.

Ein ähnlicher Funktionsname reicht nicht für **Fertig**. Beispielsweise ist eine Dashboard-Umfrage nicht automatisch eine Discord-Umfrage; eine Fallakte ist nicht automatisch ein vollständiges Einspruchssystem.

## Gesamtübersicht

| Status | Anzahl |
|---|---:|
| Fertig | 79 |
| Teilweise | 98 |
| Offen | 0 |
| **Gesamt** | **177** |

## Ergebnis nach Bereichen

| Bereich | Fertig | Teilweise | Offen |
|---|---:|---:|---:|
| Discord-Bot – Bedienung und Benutzerfreundlichkeit | 7 | 3 | 0 |
| Discord-Bot – Moderation und Sicherheit | 4 | 11 | 0 |
| Discord-Bot – Tickets und Support | 15 | 0 | 0 |
| Discord-Bot – Community und BOCHUM RP | 5 | 10 | 0 |
| TeamOS-Dashboard – Startseite und Navigation | 6 | 9 | 0 |
| Teamverwaltung und Mitarbeiterakten | 6 | 6 | 0 |
| Dienstplanung und Aktivität | 4 | 8 | 0 |
| Bewerbungen und Recruiting | 3 | 8 | 0 |
| Academy und Wissensmanagement | 4 | 6 | 0 |
| Discord und Dashboard verbinden | 0 | 10 | 0 |
| Pulse Intelligence | 6 | 9 | 0 |
| Automatisierungen und Workflow-System | 9 | 3 | 0 |
| Admin Center, Sicherheit und Betrieb | 10 | 10 | 0 |
| BOCHUM-RP-spezifische Funktionen | 0 | 5 | 0 |

## Einzelbewertung

### 1. Discord-Bot – Bedienung und Benutzerfreundlichkeit (1–10)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 1 | Interaktive, rechteabhängige Hilfe | **Fertig** | `/pulse-hilfe` wird aus dem Command-Tree erzeugt und nach Berechtigungen gefiltert; `tests/test_command_help.py`. |
| 2 | Persönliches Pulse-Profil | **Fertig** | `/profil` zeigt eigene Rolle, Dienst, Wochenzeit, Aufgaben, Tickets und Schulungen privat; `tests/test_profile_command.py`. |
| 3 | Persönliche Übersicht `/pulse` | **Fertig** | `/pulse` zeigt die vollständige persönliche Übersicht (PR #192); `tests/test_pulse_overview_command.py`. |
| 4 | Intelligente Autovervollständigung | **Teilweise** | Zusätzliche Autocomplete-Flows für verknüpfte Ticketreferenzen (PR #216) sowie Kategorien/Prioritäten bei Spieler-Meldungen (PR #219) sind getestet. Noch nicht alle passenden Befehle und Team-/Fallreferenzen bieten durchgängige Vorschläge. |
| 5 | Kontextbezogene Fehler- und Rechtehilfe | **Fertig** | `pulse_command_errors.py` erklärt fehlende Rechte, Cooldowns, Transformer- und unerwartete Fehler; `tests/test_command_errors.py`. |
| 6 | Interaktive Menüs für häufige Aktionen | **Teilweise** | Buttons, Selects und Modals sind in Ticket-, Moderations-, Dienst- und Voice-Flows vorhanden; es gibt aber kein einheitliches persönliches Aktionsmenü für alle Bereiche. |
| 7 | Persönliche Befehlsfavoriten | **Fertig** | Persistente Slash-Command-Favoriten je Nutzer (PR #194); `pulse_command_favorites.py`, `tests/test_pulse_command_favorites.py`. |
| 8 | Persönliche Benachrichtigungseinstellungen | **Fertig** | Kategorien, Opt-in-DMs, Inbox-Gruppierung und Einstellungen sind in `pulse_notify_prefs.py`; `tests/test_notify_prefs.py`. |
| 9 | Persönliche und serverweite Ruhezeiten | **Fertig** | Persönliche und globale Quiet Hours inklusive Mitternachtswechsel, Zurückhalten und Bündeln; `tests/test_notify_prefs.py`. |
| 10 | Mehrsprachige Bot-Antworten | **Teilweise** | PR #214 ergänzt eine getestete, nutzerbezogene Locale-Präferenz (Deutsch/Englisch/Auto) und übersetzt bereits /profil und /pulse-hilfe; weitere Bot-Antworten, Modals/Buttons und Dashboard-Texte sind noch nicht durchgängig migriert. |

### 2. Discord-Bot – Moderation und Sicherheit (11–25)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 11 | Gemeinsame Moderations-Fallnummern | **Teilweise** | `pulse_mod_cases.py` und `pulse_ultimate.py` vergeben IDs/Fallnummern; Teamwarnungen, KI-Moderation und Bürger-Moderation sind aber noch getrennte Fachakten. |
| 12 | Durchgängiger Moderationsverlauf | **Teilweise** | `/mod akte`, Falllisten, Warnhistorie und Ultimate-Fallereignisse existieren; ein einheitlicher Verlauf über alle Moderationsarten fehlt. |
| 13 | Versionierte Begründungsvorlagen | **Fertig** | Verwaltbarer, versionierter Vorlagenkatalog (PR #190); `pulse_mod_reason_templates.py`, `tests/test_mod_reason_templates.py`. |
| 14 | Beweismittelverwaltung | **Teilweise** | Bürger-Moderationsfälle speichern Beweisangaben; Ultimate-Fälle unterstützen Uploads und Dateimetadaten. Die Beweisverwaltung ist nicht über alle Falltypen vereinheitlicht. |
| 15 | Formelles Einspruchssystem | **Teilweise** | Moderations-DMs enthalten einen Einspruchsweg/-hinweis (`appeal_text`); ein vollständiger Einreichungs-, Fristen-, Prüfer- und Entscheidungsprozess wurde nicht gefunden. |
| 16 | Eskalationshinweise bei Wiederholungen | **Teilweise** | Warnstufen, Eskalationsvorschau und KI-Moderationsprüfung existieren; ein einheitlicher, erklärbarer Wiederholungs-Hinweis über alle Fallarten fehlt. |
| 17 | AutoMod-Testmodus ohne Sanktionen | **Fertig** | Der explizite `test`-Modus wertet echte Nachrichten aus, protokolliert Treffer getrennt und führt keine Löschungen, DMs oder Timeouts aus; `cogs/automod.py`, `pulse_moderation.py`, `tests/test_guard_admin.py` (PR #205). |
| 18 | Ausnahmen je Kanal und Rolle | **Teilweise** | AutoMod berücksichtigt Ausnahmen wie Moderatoren; die gewünschte vollständig konfigurierbare Kanal-/Rollen-Ausnahmematrix ist nicht durchgängig vorhanden. |
| 19 | Konfigurierbare Raid-Alarmstufen | **Teilweise** | `pulse_raid.py` erkennt Join-Wellen, junge Accounts und Spam; getrennte, konfigurierbare Alarmstufen mit Eskalationsablauf fehlen. |
| 20 | Notfall-Lockdown | **Teilweise** | Anti-Nuke kann einen bestätigten, begründeten und reversiblen Lockdown starten/beenden; zeitliche Ablaufsteuerung und mehrstufige Freigabe fehlen. |
| 21 | Sicherheitsübungen und Simulationen | **Teilweise** | Anti-Nuke-Regelsimulation und Replay-Tests existieren (`tests/test_antinuke_control_center.py`, `tests/test_antinuke_replay.py`); ein durchgängiger Übungsmodus für AutoMod, Raid und Lockdown fehlt. |
| 22 | Alarm bei kritischen Rollen-/Kanaländerungen | **Fertig** | Anti-Nuke verarbeitet Role-, Channel-, Permission- und Guild-Update-Ereignisse; Tests prüfen Berechtigungsänderungen und harmlose Änderungen. |
| 23 | Webhook-Überwachung | **Teilweise** | `cogs/antinuke.py` verarbeitet `on_webhooks_update`; ein separat abgesicherter, umfassend getesteter Webhook-Alarm-/Änderungsverlauf fehlt. |
| 24 | Moderationsstatistiken | **Teilweise** | Falllisten, Warnzähler, Audit und Analytics liefern Teilkennzahlen; eine vollständige Moderationsauswertung nach Typ, Bearbeitungszeit und Ergebnis fehlt. |
| 25 | Fehlalarmprüfung und Feedback für AutoMod | **Fertig** | PR #215 ergänzt eine persistente, servergebundene Fehlalarmprüfung für echte AutoMod-Ereignisse, /automod-fehlalarm und /automod-feedback, Berechtigungsprüfungen und Audit-Einträge. Begründungen werden gespeichert, aber keine Sanktion automatisch zurückgenommen und keine Regel automatisch geändert; Regressionstests und Pflicht-CI sind grün. |

### 3. Discord-Bot – Tickets und Support (26–40)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 26 | Intelligente Ticketverteilung | **Fertig** | Neue Tickets können optional automatisch dem am wenigsten belasteten, für die Kategorie zuständigen Teammitglied mit Ticket-Recht zugewiesen werden: prioritätsgewichtete Last, Kapazitätsgrenze, deterministisch, Standard aus. Teammitglieder mit aktiver Abmeldung werden übersprungen (Datum in Europe/Berlin, erster und letzter Tag zählen); sind alle abwesend oder ausgelastet, bleibt das Ticket unzugewiesen. `pulse_ticket_distribution.choose_assignee`/`active_absence_ids`, `cogs/tickets.choose_ticket_auto_assignee`; `tests/test_ticket_distribution.py`, `tests/test_ticket_workflow.py`, `tests/test_ticket_assignment_absences.py`. PR #213 gemergt in main. Einschränkung: der Dienststatus (im Dienst / nicht im Dienst) fließt bewusst nicht ein, damit Tickets auch außerhalb von Schichten verteilt werden. |
| 27 | Ticket-SLA je Kategorie | **Fertig** | Je Kategorie eigene Schwellen für Übernahme, Team-Antwort, Ersteller-Antwort und Eskalation; leere Felder erben den globalen Wert, 0 schaltet die Regel für die Kategorie aus. Dieselbe Auflösung (`category_automation_settings`) steuert Erinnerungen, Triage und den Reiter „Überfällig“. Speichern nur für Administratoren (`POST /tickets/automation`). `pulse_ticket_design.py`, `pulse_ticket_triage.py`, `pulse_ticket_builder.py`; `tests/test_pulse_ticket_design.py`, `tests/test_ticket_workflow.py`, `tests/test_pulse_ticket_triage.py`. |
| 28 | Überfälligkeitsalarm | **Fertig** | Zweistufig: 1. Erinnerung an die zuständigen Rollen bzw. den Bearbeiter, 2. Eskalation an die Ticket-Führungsebene, wenn die Erinnerung nach der eingestellten Zeit folgenlos blieb (Protokollkanal mit Rollen-Ping, Pulse-Inbox nur für Berechtigte, Eintrag in Ticket-Historie und Audit, Markierung „Eskaliert“ in der Ticket-Zentrale). Einmal je Situation, je Kategorie einstellbar, Standard aus; Pulse ändert dabei weder Bearbeiter noch Priorität. `pulse_ticket_design.automation_actions`, `cogs/tickets.escalate_overdue_ticket`; `tests/test_ticket_escalation.py`, `tests/test_ticket_workflow.py`. PR #212 gemergt in main. |
| 29 | Ticket-Übergabe mit Pflichtnotiz | **Fertig** | `/ticket uebergeben` verlangt eine Notiz und protokolliert die Übergabe; `tests/test_ticket_extras.py`. |
| 30 | Schnellantworten | **Fertig** | Kategoriebezogene Ticketantworten mit Autocomplete; `tests/test_ticket_extras.py`. |
| 31 | Interne Ticketnotizen | **Fertig** | `/ticket notiz` und `/ticket notizen` speichern/zeigen Teamnotizen getrennt von öffentlichen Nachrichten und `ticket_events`; erneute Rechteprüfung beim Modal-Submit. Backup, personenbezogener Export und Regressionstests: `pulse_ticket_extras.py`, `pulse_db.py`, `pulse_retention.py`, `tests/test_ticket_extras.py`, `tests/test_privacy.py`. |
| 32 | Ähnliche Tickets vorschlagen | **Fertig** | `/ticket aehnliche` vergleicht den aktuellen Kanalverlauf lokal mit maximal 80 archivierten Transkripten derselben Guild; Jaccard-Ähnlichkeit mit Kategoriegewichtung, begrenzte Dateigröße, sichere Pfadprüfung und kein externer KI-Dienst. Regressionsnachweis: `tests/test_ticket_extras.py`. |
| 33 | Tickets miteinander verknüpfen | **Fertig** | Persistente bidirektionale Verknüpfungen gleicher Guild mit `/ticket verknuepfen`, `/ticket verknuepfungen`, `/ticket trennen`; Dubletten-/Selbst-/Cross-Guild-Schutz, interne Notiz, Backup und personenbezogener Export. Tests: `tests/test_ticket_extras.py`, `tests/test_privacy.py`. |
| 34 | Freiwillige Ticket-Zufriedenheit | **Fertig** | Ticket-Rating-Endpunkt und Auswertung vorhanden; `tests/test_ticket_extras.py` prüft Rating-Report. Voice-Support besitzt zusätzlich eigene Bewertungen. |
| 35 | Abgeschlossene Tickets wiedereröffnen | **Fertig** | Wiederöffnen in einem neuen privaten Kanal nur mit „Tickets verwalten“; einstellbare Frist in Tagen (0 = unbegrenzt, Administratoren dürfen immer), serverseitig geprüft. Ein wiedergeöffnetes Ticket beginnt eine neue Wartezeit: Erinnerungen, Eskalation und Triage rechnen ab dem Wiederöffnen, alte Nachrichten zählen nicht mehr; die Zahl der Wiederöffnungen wird mitgeführt. Abgelehnte Datenbankänderung löscht den neuen Kanal wieder. `webserver.py` (`POST /tickets/action`), `pulse_db.reopen_ticket`, `pulse_ticket_design.reopen_allowed`/`ticket_activity`; `tests/test_ticket_workflow.py`, `tests/test_ticket_escalation.py`, `tests/test_ticket_center_workload.py`. PR #212 gemergt in main. Einschränkung: nur im Dashboard – der Discord-Kanal existiert nach dem Schließen nicht mehr. |
| 36 | Ticket-Zusammenfassung | **Fertig** | `/ticket zusammenfassung` liefert privat eine Übergabe mit Sachstand, offenen Fragen, erledigten Schritten, Beteiligten und nächstem Schritt, optional als interne Notiz. `pulse_summaries.py`, `cogs/ticket_tools.py`; `tests/test_summaries.py`. PR: Zusammenfassungs-Block. Einschränkung: ausgewertet werden die letzten 100 Textnachrichten. |
| 37 | Automatische Ticket-Triage | **Fertig** | `pulse_ticket_triage.py` bewertet Wartezeit, Priorität und Zuständigkeit; `tests/test_pulse_ticket_triage.py`. |
| 38 | Ticket-Statistiken | **Fertig** | `/ticketstats`, Ticketcenter, Rating-Report und Analytics stellen Ticketkennzahlen bereit. |
| 39 | Support-Warteschlange | **Fertig** | Voice-Warteschlange mit Position, Status und Übernahme sowie Ticket-Wartestatus sind vorhanden; `tests/test_voice_queue.py`. |
| 40 | Support-Auslastung und Überlastungshinweise | **Fertig** | Die Ticket-Zentrale zeigt Führungskräften offene Tickets je Bearbeiter, prioritätsgewichtete Last und Warnungen am konfigurierten Kapazitätslimit; `pulse_ticket_distribution.workload_snapshot`, `pulse_ticket_center.py` und Verhaltenstests. |

### 4. Discord-Bot – Community und BOCHUM RP (41–55)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 41 | Discord-Eventverwaltung mit Anmeldung | **Teilweise** | Der Team-Kalender in `pulse_ultimate.py` kann Termine/Events verwalten; ein nativer Discord-Event- und RSVP-Button-Flow ist nicht vollständig nachgewiesen. |
| 42 | Teilnehmerlisten für Events | **Fertig** | Zu-/Absagen je Kalendertermin in `ultimate_calendar_rsvps` (PR #195); `tests/test_ultimate_event_rsvps.py`. |
| 43 | Event-Erinnerungen | **Teilweise** | Interview-/Recruiting-Erinnerungen und allgemeine Automationen existieren; ein durchgängiger Event-Erinnerungsdienst fehlt. |
| 44 | Umfragen | **Teilweise** | `/ultimate/polls` bietet Single-/Multiple-Choice-Abstimmungen im Dashboard; native Discord-Umfragen und Bot-Commands sind nicht vollständig integriert. |
| 45 | Community-Vorschlagsbox | **Fertig** | PR #217 ergänzt Discord-Modal-Einreichung, servergebundene Speicherung, Dashboard-Prüfung/Freigabe und Community-Abstimmung mit Ein-Stimme-pro-Mitglied-Regel sowie Ergebnisübersicht. Verhaltenstests, Datenschutzexport und Pflicht-CI sind grün. |
| 46 | Feedbacksystem | **Fertig** | PR #217 ergänzt /feedback für anonyme oder namentliche Einreichungen, trennt allgemeines Feedback sauber von abstimmbaren Vorschlägen und zeigt beide Typen im Dashboard. Verhaltenstests, Datenschutzexport und Pflicht-CI sind grün. |
| 47 | Regelwerk-Assistent mit Quellen | **Teilweise** | Support Assistant und `support_knowledge.md` liefern Wissensantworten; zitierte, versionierte Regelwerkspassagen mit direkten Quellenlinks fehlen. |
| 48 | Freigegebene Fraktionsübersicht | **Fertig** | Fraktionsverwaltung mit offiziellen Fraktionen und öffentlicher, freigabegesteuerter Darstellung; `tests/test_factions.py`, `tests/test_public_site.py`. |
| 49 | Spieler-Meldesystem | **Fertig** | PR #219 ergänzt /spieler-meldung und /meine-meldungen, strukturierte Kategorien/Prioritäten, optionale Belege, servergebundene Fall-IDs, Team-Benachrichtigungen, Dashboard-Bearbeitung und personenbezogenen Export für Melder, Bearbeiter und Prüfer. Regression, Datenschutztests und Pflicht-CI sind grün. |
| 50 | Strikte IC-/OOC-Falltrennung | **Teilweise** | Fallkategorien und IC-/OOC-Angaben existieren in Bewerbungs-/Moderationsabläufen; ein einheitliches, validiertes IC-/OOC-Vorgangsmodell fehlt. |
| 51 | Team-Besprechungsmodus | **Teilweise** | Kalender und Besprechungseinträge existieren; Agenda, Teilnehmer, Beschlüsse und daraus erzeugte Aufgaben sind nicht als vollständiger Meeting-Workflow verbunden. |
| 52 | Ankündigungsplaner | **Teilweise** | Team-News, Ankündigungsarchiv, Social-Promotion und zeitgesteuerte Automationen existieren; ein eigener Entwurf-/Freigabe-/Sendeplaner für Ankündigungen fehlt. |
| 53 | Lesebestätigungen für Ankündigungen | **Teilweise** | Team-News besitzen Lesestatus/Bestätigungen; eine einheitliche Bestätigung für alle Arten von Dienstanweisungen und Discord-Ankündigungen ist nicht vorhanden. |
| 54 | Social-Media-Monitor | **Teilweise** | YouTube-/TikTok-Quellen, RSS-/Feedabruf, automatische Ankündigungen und SSRF-Schutz existieren; Filter- und Reportingumfang ist noch nicht vollständig. |
| 55 | Spieler-Selbstservice | **Teilweise** | Ticket-Eröffnung, `/pulse`, `/profil`, `/me` und Benachrichtigungseinstellungen decken Teilbereiche ab; eine einheitliche Selbstservice-Übersicht für Spieler mit eigenen Tickets, Events und Einstellungen fehlt. |

### 5. TeamOS-Dashboard – Startseite und Navigation (56–70)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 56 | Personalisierte Startseite | **Fertig** | `/me` und persönliche Login-Startseite; `tests/test_start_page.py`. |
| 57 | Konfigurierbares Widget-System | **Teilweise** | Sichtbarkeit der vorhandenen `/me`-Widgets kann gespeichert werden; Drag-and-drop, Reihenfolge, Größen und frei definierbare Widgets fehlen; `tests/test_home_widgets.py`. |
| 58 | Live-Aktivitätsfeed | **Teilweise** | Audit, Team-Timeline, Inbox und Aktivitätsdaten existieren; ein durchgehend live aktualisierter Feed über alle Ereignisse fehlt. |
| 59 | Command Palette mit Strg+K | **Fertig** | Globale, rechtegefilterte Command-Palette (PR #193); `tests/test_command_center_ui.py`. |
| 60 | Dashboard-Favoriten | **Fertig** | Persönliche Seitenfavoriten unter `/me/favorites/*` (PR #191); `pulse_home.py`, `tests/test_start_page.py`, `tests/test_home_widgets.py`. |
| 61 | Gespeicherte Filter und Ansichten | **Fertig** | Persönliche Filter je Dashboard-Nutzer für Tickets, Bewerbungen, Teamakten, Bürger-Moderation und Automationen: speichern, anwenden, ersetzen, löschen (höchstens 12 je Seite). Teamakten haben dafür eine serverseitige Namens- und Statusfilterung erhalten. Tabelle `dashboard_saved_views` im JSON-Backup und im Personenexport. `pulse_views.py`, `pulse_db.py`, `pulse_ticket_center.py`, `pulse_ultimate.py`, `webserver.py`; `tests/test_saved_views.py`. PR: Ansichten-Block. Einschränkung: Ansichten sind persönlich, noch nicht rollenweit teilbar (siehe #69). |
| 62 | Globale Suche | **Teilweise** | `pulse_suite` stellt eine globale Suche über mehrere TeamOS-Datenbereiche bereit; Vollständigkeit über alle Tickets, Wiki-Versionen, Bewerbungen und Auditdaten ist nicht belegt. |
| 63 | Rollenabhängige Navigation | **Fertig** | Navigation wird nach Rechten gefiltert; `tests/test_dashboard_redesign.py` prüft Berechtigungsfilter und sensitive Menüpunkte. |
| 64 | Mobile Dashboard-Version | **Teilweise** | Responsive Layouts und mobile E2E-Tests existieren; nicht alle Dashboard-Workflows sind vollständig auf Mobilgeräten geprüft. |
| 65 | Einheitliches Designsystem | **Teilweise** | Gemeinsame Design-Tokens und Komponenten (`pulse_design.py`, `static/pulse.css`) sind vorhanden; ältere Seiten sind nicht vollständig vereinheitlicht. |
| 66 | Dark-Mode-Feinschliff | **Teilweise** | Theme-aware Klassen und gemeinsame Farbpalette existieren; konsistente Darstellung jeder Legacy-Seite ist nicht vollständig belegt. |
| 67 | Tastaturbedienung | **Teilweise** | Einzelne zugängliche Navigationselemente/Fokuszustände sind getestet; durchgängige Tastaturbedienung aller Arbeitsabläufe fehlt. |
| 68 | Kontextbezogene Dashboard-Hilfen | **Teilweise** | Fehler- und Rechtehinweise sind vorhanden; eine vollständige kontextuelle Hilfe für komplexe Einstellungen/Formulare fehlt. |
| 69 | Benutzerdefinierte Ansichten | **Fertig** | Bis zu acht persönliche „Mein Pulse“-Layouts speichern Widget-Sichtbarkeit, Reihenfolge und Spalten; Nutzergrenzen, Allowlist und Rückfall auf bestehende Widgets sind getestet (PR #206). |
| 70 | Echtzeit-Statusanzeigen | **Teilweise** | Health-, Voice- und Live-Console-Status existieren; kein einheitlicher Echtzeit-Transport für alle Dashboardbereiche. |

### 6. Teamverwaltung und Mitarbeiterakten (71–82)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 71 | 360°-Teamakte | **Teilweise** | Teamprofile, Warnungen, Schichten, Aufgaben, Schulungen, Score und Timeline sind auf mehrere Module verteilt; keine vollständig konsolidierte 360°-Akte. |
| 72 | Chronologische Mitarbeiter-Timeline | **Fertig** | `pulse_timeline.py` baut eine chronologische, rechtegeprüfte Timeline; `tests/test_timeline.py`. |
| 73 | Individuelle Teamprofile | **Teilweise** | Team-/Ultimate-Profilseiten mit Status und Kennzahlen existieren; sämtliche Teamdaten sind noch nicht in einer einheitlichen Profilansicht gebündelt. |
| 74 | Kompetenzübersicht | **Teilweise** | Schulungszuweisungen und bestandene Trainings sind erfasst; eine rollen-/kompetenzbasierte Matrix fehlt. |
| 75 | Mitarbeiterentwicklung | **Teilweise** | Beförderungen, Probezeiten, Warnungen und Schulungen bilden Teile der Entwicklung ab; individuelle Entwicklungspläne fehlen. |
| 76 | Beförderungsvorbereitung | **Teilweise** | Rangfortschritt, Aktivität, Aufgaben und Team-Score können Führungskräften Hinweise geben; ein vollständiges, begründetes Entscheidungspaket fehlt. |
| 77 | Mehrstufige Beförderungsprüfung | **Fertig** | 4-Augen-Freigaben und getrennte Entscheidungsschritte für Beförderungen sind vorhanden; Rechte-/Warnregressionen sind durch Teamwarn-/Promotiontests abgedeckt. |
| 78 | Team-Onboarding | **Fertig** | Onboarding-Checklisten werden beim Eintritt angelegt und können mit Mentor bearbeitet werden; `tests/test_checklists.py`. |
| 79 | Team-Offboarding | **Fertig** | Offboarding-Checklisten mit automatisch abgehakten Pulse-Schritten und Rechteschutz; `tests/test_checklists.py`. |
| 80 | Kompetenzmatrix | **Fertig** | Rollenbezogene Kompetenzmatrix unter `/training/matrix` mit Quiznachweisen, Punktzahl/Datum und Qualifikationsstufen; managergeschützt und rein lesend (PR #207). |
| 81 | Team-Auslastung | **Teilweise** | Aufgaben, Tickets, Schichten und Team-Score liefern Belastungsindikatoren; eine einheitliche, aktuelle Arbeitslastansicht fehlt. |
| 82 | Automatische Arbeitslastverteilung | **Fertig** | Führungskräfte können unzugewiesene Aufgaben anhand prioritätsgewichteter Aufgaben- und Ticketlast verteilen; bestehende Zuweisungen bleiben unangetastet und der Vorgang wird protokolliert (PR #208). |

### 7. Dienstplanung und Aktivität (83–94)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 83 | Schichtplanung | **Teilweise** | Schichtstart/-pause/-ende und geplante Team-Schichten existieren; Planungs-/Änderungsablauf ist noch nicht durchgängig mit Tests abgesichert. |
| 84 | Schichttausch | **Fertig** | Persönlicher Antrag, Zustimmung der Gegenseite und Leitungsfreigabe; atomare Konfliktprüfung und Datenschutzexport (PR #209). |
| 85 | Dienstabdeckung | **Teilweise** | Dienstplan und Teamstatus sind vorhanden; automatische Lücken-/Mindestbesetzungsprüfung fehlt. |
| 86 | Dienst-Erinnerungen | **Teilweise** | Activity Checks, Abwesenheiten und allgemeine Jobs erinnern; gezielte Erinnerungen vor persönlichen Schichten sind nicht vollständig belegt. |
| 87 | Abwesenheitskalender | **Teilweise** | Abmeldungen und Team-Kalender existieren; eine vollständig zusammengeführte persönliche Abwesenheits-/Schichtansicht ist nicht nachgewiesen. |
| 88 | Wochen- und Monatsberichte | **Teilweise** | Analytics, Wochen-/Monatskennzahlen und Exporte existieren; automatische, einheitliche Teamberichte mit nachvollziehbarer Definition aller KPIs fehlen. |
| 89 | Dienstzeit-Anomalien | **Teilweise** | Lange Schichten und Datenintegritätsprobleme werden erkannt; statistische Anomalieerkennung mit erklärbaren Schwellen fehlt. |
| 90 | Schichtnotizen | **Fertig** | Schichtbezogene Notizen samt Widget auf „Mein Pulse“ (PR #198); `tests/test_shift_notes.py`. |
| 91 | Übergabeprotokolle | **Fertig** | `/ultimate/handovers` unterstützt Titel, Inhalt, Priorität, Empfänger, Status, Benachrichtigung und Archivierung. |
| 92 | Team-Verfügbarkeitsmatrix | **Teilweise** | Schichtplan, Dienststatus und Abmeldungen liefern Grunddaten; keine Matrix mit wiederkehrender Verfügbarkeit und Abdeckungsprognose. |
| 93 | Aktivitätsentwicklung | **Fertig** | Historische Schichtdaten, Wochenvergleiche und Trendfunktionen im Cockpit/Analytics; `tests/test_pulse_cockpit_utils.py`. |
| 94 | Mehrdimensionale Leistungsübersicht | **Teilweise** | Team-Score und Leaderboards nutzen mehrere Signale; Gewichtung/Erklärung und faire Vergleichbarkeit sind noch begrenzt. |

### 8. Bewerbungen und Recruiting (95–105)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 95 | Kanban-Bewerbungspipeline | **Teilweise** | `pulse_board.py` bildet Bewerbungsstufen ab; eine vollständig interaktive Kanban-Pipeline ist nicht belegt. |
| 96 | Bewerbungszuweisung | **Teilweise** | Bewerbungsprüfer und Zuständigkeiten sind im Recruitingablauf vorhanden; eine durchgängige, explizite Zuweisung mit Lastverteilung ist nicht vollständig. |
| 97 | Mehrere unabhängige Prüfer | **Teilweise** | Interview- und Bewertungsdatensätze existieren; ein vollständiger paralleler Multi-Reviewer-Prozess mit getrennten Freigaben fehlt. |
| 98 | Interview-Bewertungsbogen | **Fertig** | Interviewformular mit mehreren Kriterien, Noten und Notizen; `tests/test_application_eligibility.py` prüft den zweistufigen Interviewablauf. |
| 99 | Interview-Kalender | **Teilweise** | Interviews und Team-Kalender existieren; Interviewtermine sind nicht vollständig als Kalenderobjekte mit Teilnehmern/Erinnerungen verbunden. |
| 100 | Bewerber-Kommunikationshistorie | **Teilweise** | Bewerbungsstatus, Interviewinformationen und Kommunikationsvorlagen sind vorhanden; ein vollständiges, revisionsfestes Kommunikationsjournal fehlt. |
| 101 | Automatische Recruiting-Erinnerungen | **Teilweise** | `pulse_app_comms.due_reminders()` berechnet fällige Erinnerungen; die vollständige Zustellung/Fehlerabdeckung für alle Recruitingfälle ist nicht gesondert belegt. |
| 102 | Recruiting-Statistik | **Teilweise** | Bewerbungsstufen und Analytics existieren; vollständige Conversion-, Wartezeit- und Prüferstatistik fehlt. |
| 103 | Zusage-/Absagevorlagen | **Teilweise** | `pulse_app_comms` enthält Vorlagen/Rendering; eine zentral verwaltete, versionierte Vorlagenbibliothek mit allen Entscheidungsschritten fehlt. |
| 104 | Bewerbungs-Dublettenprüfung | **Fertig** | Sichere Dublettenhinweise mit prüfbaren Indizien, keine automatische Entscheidung (PR #200); `pulse_application_duplicates.py`, `tests/test_application_duplicate_detection.py`. |
| 105 | Einstellungs-Checkliste | **Fertig** | Onboarding-Checkliste wird in den Join-/Hire-Pfaden erzeugt; `tests/test_checklists.py`. |

### 9. Academy und Wissensmanagement (106–115)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 106 | Interne Lernplattform | **Teilweise** | `/training` und Wiki existieren; die vollständige Academy mit Lernpfaden, Zertifikaten, Feedback und Versionskontrolle fehlt. |
| 107 | Lernpfade je Rolle | **Fertig** | Rollenbezogene Lernpfade mit Fortschritt (PR #201); `tests/test_training_learning_paths.py`. |
| 108 | Quizsystem | **Teilweise** | Trainingserstellung, Fragen, Zeitlimit und Bestehensgrenze existieren; vollständige Quizverwaltung und direkte End-to-End-Abdeckung sind begrenzt. |
| 109 | Zertifikate | **Teilweise** | `/ultimate/certificates/create` kann Zertifikate manuell vergeben; automatische Ausstellung nach Kursabschluss und Nachweisverifikation fehlen. |
| 110 | Auffrischungsschulungen | **Teilweise** | Zertifikate besitzen `valid_until`; automatische Ablaufwarnung und Auffrischungszuweisung sind nicht nachgewiesen. |
| 111 | Wissensdatenbank/Wiki | **Teilweise** | `/wiki` unterstützt Listen-, Detail- und Speicherfunktionen; Suche, redaktionelle Freigaben und vollständige Versionskontrolle fehlen. |
| 112 | Artikel-Versionierung | **Fertig** | Unveränderliche Wiki-Versionen und Wiederherstellung (PR #196); `tests/test_wiki_versioning.py`. |
| 113 | Lesebestätigung für Dienstanweisungen | **Teilweise** | Team-News haben Lesestatus; versionierte Dienstanweisungen mit gesonderter Kenntnisnahme fehlen. |
| 114 | Schulungsfortschritt | **Fertig** | Trainingsversuche und Zuweisungen werden gespeichert; bestandene Prüfungen setzen passende Zuweisungen auf `passed`; `tests/test_training_assignments.py`. |
| 115 | Schulungsfeedback | **Fertig** | Anonymes Feedback je Schulung (PR #197); `tests/test_training_feedback.py`. |

### 10. Discord und Dashboard verbinden (116–125)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 116 | Gemeinsame Fallnummern | **Teilweise** | `pulse_db.record_event()` unterstützt Korrelations-IDs; eine gemeinsame ID über Ticket-, Moderations-, Spieler- und Beschwerdefälle ist noch nicht durchgängig. |
| 117 | Echtzeit-Synchronisierung beider Oberflächen | **Teilweise** | Gemeinsame DB-/Auditdaten existieren; ein zentraler Push-/Eventbus mit konsistentem Echtzeit-Fallback ist nicht vorhanden. |
| 118 | Gemeinsame Benachrichtigungs-Inbox | **Teilweise** | Pulse Inbox und Benachrichtigungspräferenzen existieren; nicht alle Bot-/Dashboard-Events werden über einen einheitlichen Contract zugestellt. |
| 119 | Direktlinks zwischen Discord und Dashboard | **Teilweise** | Bot-Embeds und Befehle verlinken `/me`, Tickets und Pulse-Seiten; universelle Fall-/Vorgangslinks fehlen. |
| 120 | Gemeinsame Berechtigungsprüfung | **Teilweise** | Gemeinsame Rollen-/Rechtehelfer existieren; Bot-Checks und Web-Rechteberechnung sind noch nicht vollständig ein einziger zentraler Policy-Service. |
| 121 | Discord-Aktionskarten | **Teilweise** | Buttons, Selects, Modals und Bestätigungsdialoge werden genutzt; keine einheitliche Aktionskarten-API für alle TeamOS-Vorgänge. |
| 122 | Gemeinsame Audit-Historie | **Teilweise** | `pulse_db`-Events, Suite-Audit und Modul-Audits existieren; nicht jeder relevante Schreibvorgang folgt einem gemeinsamen Ereignisschema. |
| 123 | Zentrale Vorlagenbibliothek | **Teilweise** | Ticketantworten, Recruiting-Kommunikation, RP-Ankündigungen und KI-Vorlagen existieren separat; eine gemeinsame verwaltete Bibliothek fehlt. |
| 124 | Gemeinsame Aufgaben in Bot und Dashboard | **Teilweise** | Discord-Befehle und Dashboard lesen/schreiben Pulse-Aufgaben; vollständige Synchronisierung, gemeinsame Aktionsrechte und Übergaben sind noch nicht überall getestet. |
| 125 | Zentrale Ereignisverarbeitung | **Teilweise** | Automation-Engine verarbeitet ausgewählte Anti-Nuke-/Systemereignisse; ein Domain-Event-Vertrag für alle Pulse-Module fehlt. |

### 11. Pulse Intelligence (126–140)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 126 | Team-Ausfall-Simulation | **Fertig** | Pulse Intelligence bietet eine begrenzte, lesende Team-Ausfall-Simulation; `tests/test_pulse_intelligence.py`. |
| 127 | Ticketlast-Prognose | **Teilweise** | Ticket-Surge-Simulation und Tickettrendvergleich existieren; echte statistische Prognose mit Unsicherheit und Datenbasis fehlt. |
| 128 | Personalbedarfsprognose | **Fertig** | `/suite/forecast` vergleicht stündliche Ticket-Eingänge der letzten acht abgeschlossenen Wochen mit geplanter Personalabdeckung; begrenzt, transparent und managergeschützt (PR #210). |
| 129 | Anomalieerkennung | **Teilweise** | Rule-based Findings zu SLA, Systemzustand und Volumen existieren; keine umfassende Anomalieerkennung über alle Datenbereiche. |
| 130 | Automatische Tagesberichte | **Teilweise** | Team-Report-Automationsschritte und Copilot-Zusammenfassungen existieren; kein fertig vorkonfigurierter, täglicher geprüfter Berichtslauf. |
| 131 | Automatische Wochenberichte | **Teilweise** | Wochenkennzahlen und Reports existieren; automatischer Wochenbericht mit Trendvergleich und Datenquellen fehlt. |
| 132 | Führungskräfte-Assistent | **Teilweise** | `/ultimate/copilot` beantwortet begrenzte Fragen auf Basis lokaler Kennzahlen; keine vollständige Abfrage über alle autorisierten Pulse-Daten. |
| 133 | Wissensassistent mit Quellenangaben | **Teilweise** | Support Assistant nutzt freigegebene Wissensdateien; Quellzitate/Versionen je Antwort fehlen. |
| 134 | Ticket-Zusammenfassungen | **Fertig** | Eine gemeinsame lokale Funktion (`pulse_summaries.summarize_ticket`) für Discord und Dashboard (`GET /ticket/{id}/summary`, offene Tickets aus dem Kanal, geschlossene aus dem Transkript); rein extraktiv, jede Aussage mit Quelle. `pulse_summaries.py`, `pulse_pro.py`; `tests/test_summaries.py`. PR: Zusammenfassungs-Block. |
| 135 | Bewerbungs-Zusammenfassungen | **Fertig** | Strukturierte Übersicht je Bewerbung nur für Recruiting-Berechtigte (Umfang, Kernsätze als Zitat, Praxisaufgaben, vorhandenes Testergebnis, Hinweise zur Vollständigkeit) – ohne Bewertung, Rangfolge oder Empfehlung. `pulse_summaries.py`, `webserver.py`; `tests/test_summaries.py`. PR: Zusammenfassungs-Block. Einschränkung: nur im Dashboard. |
| 136 | Automatischer Schichtübergabe-Entwurf | **Teilweise** | Manuelle Übergaben sind vorhanden; automatische Entwürfe aus offenen Tickets, Aufgaben und Ereignissen fehlen. |
| 137 | Datenbasierte Warnhinweise | **Teilweise** | Pulse Intelligence erzeugt erklärbare Findings; Abdeckung und Schwellwerte sind noch begrenzt. |
| 138 | Was-wäre-wenn-Simulation | **Fertig** | Read-only Simulatoren und Workflow-Sandbox ohne Ausführung; `tests/test_pulse_intelligence.py`. |
| 139 | Regelbasierte Empfehlungen | **Teilweise** | Findings geben Hinweise aus Regeln/Metriken; ein vollständiger nächster-Schritt-Katalog mit bestätigbarer Aktion fehlt. |
| 140 | Lokale Intelligence ohne KI-Abhängigkeit | **Fertig** | Copilot/Simulatoren besitzen lokalen Regelmodus ohne externe KI-API; `tests/test_pulse_intelligence.py` prüft `local_rules_no_external_ai`. |

### 12. Automatisierungen und Workflow-System (141–152)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 141 | Visueller Workflow-Editor | **Teilweise** | `/ultimate/automations` bietet einen Formular-/Schritt-Editor mit Triggern und Aktionen; kein grafischer Drag-and-drop-Node-Editor. |
| 142 | Zeitgesteuerte Workflows | **Fertig** | Intervalltrigger und Scheduler sind vorhanden; `tests/test_automation_center.py`. |
| 143 | Ereignisbasierte Workflows | **Fertig** | Event-Trigger, Schwellenwerte, Deduplizierung und Anti-Nuke-Events; `tests/test_automation_center.py`. |
| 144 | Genehmigungsschritte in Workflows | **Fertig** | Jede Automation kann Freigabestufen verlangen; der Lauf startet erst nach der Freigabe, Ablehnung beendet ihn. `pulse_workflows.py`, `pulse_automation_rules.py`, `pulse_ultimate.py`; `tests/test_workflow_engine.py` (ApprovalTests). PR: Workflow-Block. Einschränkung: Freigabe gilt für den ganzen Lauf, nicht für einzelne Schritte mitten in der Kette. |
| 145 | Mehrstufige Workflow-Freigaben | **Fertig** | Bis zu drei Stufen in fester Reihenfolge, Zuständigkeit Führungskraft/Administrator, Quorum 1–5, Frist, Vier-Augen-Prinzip, jede Person einmal; Abbruch bei Ablehnung, Fristablauf, geänderter/pausierter/gelöschter Automation; Inbox-Hinweise. Dateien wie #144; `tests/test_workflow_engine.py` (ApprovalTests). PR: Workflow-Block. Einschränkung: Entscheidung nur im Dashboard, noch nicht per Discord-Button. |
| 146 | Workflow-Testmodus | **Fertig** | Manueller Testlauf, Testmodus und Dry-Run ohne Seiteneffekte; `tests/test_automation_center.py`. |
| 147 | Workflow-Fehlerbehandlung | **Fertig** | Fehlgeschlagene Schritte werden protokolliert, weitere Schritte kontrolliert behandelt; `tests/test_automation_center.py`. |
| 148 | Automatische Workflow-Wiederholungen | **Fertig** | Begrenzte Versuche (0–5), Exponential Backoff mit Obergrenze, nur fehlgeschlagene Schritte, ein Eintrag je Lauf, Fehlerprotokoll, Dead-Letter-Liste und manuelle Wiederaufnahme; unterbrochene Versuche werden nie automatisch wiederholt. `pulse_workflows.py`; `tests/test_workflow_engine.py` (RetryTests). PR: Workflow-Block. Einschränkung: Automationen im alten Legacy-JSON-Format werden nicht wiederholt. |
| 149 | Workflow-Versionierung | **Fertig** | Unveränderliche Versionen (DB-Trigger), Verlauf, Feldvergleich, kontrolliertes Wiederherstellen (neue Version, Automation pausiert, heutige Rechteprüfung); Läufe nennen ihre Version. `pulse_workflows.py`, `pulse_ultimate.py`; `tests/test_workflow_engine.py` (VersioningTests). PR: Workflow-Block. |
| 150 | Workflow-Ausführungsprotokoll | **Fertig** | Ausführungen und Schrittstatus werden in `ultimate_automation_runs` gespeichert; Tests prüfen begrenzte Historie. |
| 151 | Abhängigkeiten zwischen Workflows | **Teilweise** | Schritte laufen sequenziell; Abhängigkeiten zwischen Workflows/Jobs mit Wait-/Join-Bedingungen fehlen. |
| 152 | Workflow-Vorlagen | **Teilweise** | Standardaktionen, Trigger und bestehende Automationsregeln existieren; eine umfangreiche, versionierte Vorlagenbibliothek fehlt. |

### 13. Admin Center, Sicherheit und Betrieb (153–172)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 153 | Zentrales System-Cockpit | **Teilweise** | `/ultimate/system`, Health Center, Live Console und Cockpit zeigen Teilbereiche; eine einzelne vollständige Betriebskonsole für alle Komponenten fehlt. |
| 154 | Discord-Bot-Ausfallalarm | **Teilweise** | `pulse_alerts` kann Fehler/Alarme bündeln und an einen konfigurierten Kanal senden; ein unabhängiger externer Down-Detector für einen vollständig ausgefallenen Bot fehlt. |
| 155 | Automatische Fehlerzusammenfassung | **Teilweise** | Logging-Ringpuffer und Alert-Cooldowns existieren; Fehler werden nicht vollständig nach Signatur gruppiert und mit Ursachenbericht zusammengefasst. |
| 156 | Queue-Monitoring | **Fertig** | Health-/Systemübersichten erfassen Jobs, Tasks, Voice-/AI-Warteschlangen; `tests/test_health.py` und Systemchecks. |
| 157 | API-Latenzmonitoring | **Teilweise** | Gateway-Latenz und ausgewählte Service-/API-Statuswerte werden angezeigt; ein umfassendes Latenzmonitoring pro internem Endpunkt fehlt. |
| 158 | Ressourcenverlauf | **Teilweise** | Pulse Ultimate speichert begrenzte RAM-Samples und erkennt Trends; vollständige CPU-/RAM-/Disk-/Containerhistorie fehlt. |
| 159 | Konfigurationsprüfung | **Fertig** | `.env`-/Systemvalidierung und Eingabevalidierung existieren; `tests/test_env_validation.py`, `tests/test_system_checks.py`. |
| 160 | Rechte-Differenzprüfung | **Teilweise** | Rollenhierarchie, fehlende Discord-Rechte und gefährliche Permissions werden geprüft; kein vollständiger Soll-/Ist-Vergleich aller fachlichen Pulse-Rechte. |
| 161 | Backup-Übersicht | **Fertig** | Backup-Liste, Status, vollständige ZIP-Backups und Admin-Panel vorhanden; `tests/test_full_backup.py`. |
| 162 | Restore-Vorschau | **Fertig** | `preview_restore()` prüft Manifest, Dateien und Tabellen vor Restore; `tests/test_full_backup.py`. |
| 163 | Isolierte Restore-Übungen | **Fertig** | Isolierter Restore-Test und wöchentlicher Testlauf sind implementiert; `tests/test_full_backup.py`. |
| 164 | Notfallhandbuch | **Fertig** | Durchgängiges Incident-Runbook mit Rollen, P1–P4-Eskalation, Erstreaktions- und Wiederanlauf-Checklisten, sicheren Diagnose-/Restore-/Rollback-Pfaden sowie Datenschutz-/Credential-Vorgehen; docs/PULSE_NOTFALLHANDBUCH.md, durch sieben Inhaltsregressionstests abgesichert in tests/test_incident_runbook.py. |
| 165 | Admin-Aktionshistorie | **Teilweise** | Suite-Audit und Settings-History protokollieren viele Adminänderungen; vollständige Abdeckung sämtlicher Admin-Aktionen und ein einheitlicher Viewer fehlen. |
| 166 | Sicherer Wartungsmodus | **Fertig** | Globaler Wartungsmodus und Schreibsperren sind vorhanden; E2E prüft abgelehnte Schreibaktionen während Maintenance. |
| 167 | Feature-Schalter | **Fertig** | `pulse_flags`/Admin-Feature-Schalter mit persistenter Aktivierung; `tests/test_dashboard_redesign.py` und Admin-Tests. |
| 168 | Testserver-/Testdatenmodus | **Teilweise** | CI-Fakes, Preview-Server und Workflow-Testmodus existieren; ein konfigurierbarer isolierter Test-Guild-Modus für Admins fehlt. |
| 169 | Release-Übersicht | **Teilweise** | Version und Status werden angezeigt; laufender Commit, Deploymentvergleich, letzter Release und Rollback-Stand sind nicht vollständig zusammengeführt. |
| 170 | Datenintegritätsprüfung | **Fertig** | Read-only Konsistenzdiagnose, Bewerbungsdatenprüfung, Schichthistorie und SQLite-Quick-Check; `tests/test_consistency.py`, `tests/test_application_data_audit.py`, `tests/test_system_checks.py`. |
| 171 | Datenschutz-Anfragen | **Fertig** | Branch `feature/privacy-requests-171`: `pulse_privacy_requests.py` + `/admin/privacy/requests` (nur Admins) mit Auskunft/Berichtigung/Löschung/Einschränkung/Widerspruch, Frist 1 Monat (einmal um ≤ 60 Tage verlängerbar), Status, Zuständigkeit, Notizen, revisionsfestem Verlauf, lesender Datenbestandsprüfung und JSON-Auskunft; Discord `/datenschutz-anfrage` und `/datenschutz-status` (privat, nur eigene Anfragen), Admin-Inbox bei Eingang und tägliche Überfälligkeitserinnerung. Tabellen im JSON-Backup und Personenexport. `tests/test_privacy_requests.py`. Pulse löscht nichts automatisch. |
| 172 | Aufbewahrungsübersicht | **Teilweise** | `pulse_retention.py` und Phase-15.6-Retention-Dokumente enthalten Regeln/Pläne; eine vollständige UI-Matrix über jede Datenart und Frist fehlt. |

### 14. BOCHUM-RP-spezifische Funktionen (173–177)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 173 | BOCHUM-RP-Leitstelle | **Teilweise** | `/cockpit` und `/ultimate` zeigen Teamstatus, Tickets, Voice-Support, Fälle, Kalender und Betrieb; eine durchgängige Leitstelle mit allen Eskalationen/Community-Meldungen fehlt. |
| 174 | Abteilungsverwaltung | **Teilweise** | `/ultimate/departments` unterstützt Abteilungserstellung und Mitglieder hinzufügen/entfernen; Aufgaben, Schulungen, Ziele und Abteilungsstatistiken sind nicht vollständig integriert. |
| 175 | Vorgangsverwaltung | **Teilweise** | `/ultimate/cases` besitzt Fallnummern, Kategorien, Prioritäten, Zuständigkeit, Anhänge, Roblox-Spieler und Ereignisverlauf; interne Kommunikation und strukturierter Abschlussbericht fehlen. |
| 176 | BOCHUM-RP Academy | **Teilweise** | Training, Quiz, Wiki, Zuweisungen und manuell vergebbare Zertifikate existieren; Lernpfade, automatische Zertifizierung, Auffrischung und Wissensversionierung fehlen. |
| 177 | Team-Leitstand | **Teilweise** | Führungscockpit zeigt aktive Teammitglieder, Schichten, Tickets und Voice-Fälle; Verfügbarkeitsplanung, Arbeitslastverteilung und vollständiger Aufgabenleitstand fehlen. |

## Wichtige Befunde

1. **Viele Grundlagen sind schon vorhanden.** Besonders deutlich ist das in Ticketing, Moderation, Bewerbungen, Teamakten, Trainings, Backups, Analytics und den Ultimate-Modulen.
2. **Einige frühere Einschätzungen waren zu pessimistisch.** `pulse_ultimate.py` enthält echte Routen und Datenmodelle für Abteilungen, Fälle, Kalender, Umfragen, Feedback, Übergaben, Awards, Zertifikate und Automationen.
3. **Einige dieser Ultimate-Bereiche haben wenig direkte Verhaltenstest-Abdeckung.** Sie werden deshalb in diesem Audit meist als **Teilweise** eingestuft, bis die kritischen Rechte-, Daten- und Fehlerpfade durch Tests abgesichert sind.
4. **Die gemeinsamen Plattformfunktionen sind der größte Integrationsrückstand.** Korrelations-IDs, Inbox, Audit, Rechte und Events existieren jeweils in Teilen, sind aber noch nicht vollständig über alle Module vereinheitlicht.
5. **Noch offen (1):** #10 Mehrsprachige Bot-Antworten. Die übrigen 103 teilweise bewerteten Punkte enthalten weiterhin wesentliche Funktionslücken oder fehlende Integrations-/Verhaltenstests.
6. **Das Audit ist kein Deployment.** Der VPS kann weiterhin einen älteren Commit ausführen, bis ein gesondert freigegebener Deployment-Schritt durchgeführt wird.

## Technische Hauptbelege

- Discord-Hilfe und Fehler: `pulse_command_help.py`, `pulse_command_errors.py`, `tests/test_command_help.py`, `tests/test_command_errors.py`
- Persönliches Profil und Notifications: `cogs/pulse_pro.py`, `pulse_notify_prefs.py`, `tests/test_profile_command.py`, `tests/test_notify_prefs.py`
- Moderation / Anti-Nuke / Raid: `pulse_moderation.py`, `pulse_mod_cases.py`, `cogs/mod_cases.py`, `cogs/antinuke.py`, `pulse_raid.py`, dazu die Tests `test_mod_cases.py`, `test_antinuke_control_center.py`, `test_antinuke_replay.py`, `test_raid_watch.py`
- Ticketing: `cogs/tickets.py`, `cogs/ticket_tools.py`, `pulse_ticket_triage.py`, `pulse_ticket_extras.py`, `pulse_ticket_center.py`, dazu `test_ticket_extras.py`, `test_ticket_workflow.py`, `test_pulse_ticket_triage.py`
- TeamOS / Ultimate: `cogs/pulse_suite.py`, `pulse_ultimate.py`, `pulse_home.py`, `pulse_cockpit.py`, `pulse_checklists.py`, `pulse_timeline.py`
- Academy / Knowledge: `pulse_features.py` (Wiki/Training-Routen), `pulse_ultimate.py` (Zertifikate), `tests/test_training_assignments.py`
- Intelligence / Automations: `pulse_intelligence.py`, `pulse_automation_rules.py`, `pulse_ultimate.py`, `tests/test_pulse_intelligence.py`, `tests/test_automation_center.py`
- Backup / Betrieb / Integrität: `pulse_backup.py`, `pulse_retention.py`, `pulse_consistency.py`, `pulse_system_checks.py`, `tests/test_full_backup.py`, `tests/test_consistency.py`, `tests/test_system_checks.py`
- Effektive Route-Landkarte: `docs/ROUTEN.md` (wichtig: beschreibt zur Laufzeit aktive und verdrängte Handler; nicht jede ähnlich benannte Funktion ist zwangsläufig aktiv).

## Nächster sinnvoller Umsetzungsblock

Die offenen Punkte sollten nicht in einem 177-Funktionen-Monolithen umgesetzt werden. Empfohlene Reihenfolge:

1. **Locale-System (#10):** Spracheinstellung und Übersetzungsinfrastruktur; anschließend Kernbefehle und weitere Bot-Antworten systematisch migrieren.
2. **Moderation (#11–#25):** gemeinsame Fallakten, Einsprüche, Beweismittel, Ausnahmen, Raid-Stufen und Fehlalarm-Rückkanal.
3. **Community und BOCHUM RP (#41–#55):** native Discord-Events, Meeting-Workflow, Regelwerkquellen und Spieler-Selbstservice.
4. **TeamOS/Personal/Academy (#56–#115):** Live-Feed, Suche, 360°-Akte, Recruiting, Lernpfade und Wissensmanagement vervollständigen.
5. **Integration/Intelligence/Operations (#116–#177):** gemeinsame Ereignis-/Auditverträge und die noch fehlenden Leitstellen-, Admin- und Datenschutzabläufe.

Jede weitere Umsetzung sollte eine klar abgegrenzte PR mit eigenen Regressionstests, Berechtigungsprüfung und Daten-/Rollback-Betrachtung erhalten.
