# Pulse – Statusaudit der 177 Funktionsideen

**Ursprünglicher Auditstand:** `main` bei `c564dc448e647b48a649659da8c2c8b0a834301d` (Kurz-SHA `c564dc4`)  
**Aktualisierter Umsetzungsnachweis:** `main` bei `be60982` (PRs #190–#198, #200, #201 gemergt) plus Workflow-Block (#144, #145, #148, #149). Fortschritt, Phasen und nächste Blöcke: `docs/PULSE_177_IMPLEMENTATION_PROGRESS.md`.  
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
| Fertig | 58 |
| Teilweise | 110 |
| Offen | 9 |
| **Gesamt** | **177** |

## Ergebnis nach Bereichen

| Bereich | Fertig | Teilweise | Offen |
|---|---:|---:|---:|
| Discord-Bot – Bedienung und Benutzerfreundlichkeit | 7 | 2 | 1 |
| Discord-Bot – Moderation und Sicherheit | 2 | 12 | 1 |
| Discord-Bot – Tickets und Support | 9 | 6 | 0 |
| Discord-Bot – Community und BOCHUM RP | 2 | 13 | 0 |
| TeamOS-Dashboard – Startseite und Navigation | 4 | 9 | 2 |
| Teamverwaltung und Mitarbeiterakten | 4 | 6 | 2 |
| Dienstplanung und Aktivität | 3 | 8 | 1 |
| Bewerbungen und Recruiting | 3 | 8 | 0 |
| Academy und Wissensmanagement | 4 | 6 | 0 |
| Discord und Dashboard verbinden | 0 | 10 | 0 |
| Pulse Intelligence | 3 | 10 | 2 |
| Automatisierungen und Workflow-System | 9 | 3 | 0 |
| Admin Center, Sicherheit und Betrieb | 8 | 12 | 0 |
| BOCHUM-RP-spezifische Funktionen | 0 | 5 | 0 |

## Einzelbewertung

### 1. Discord-Bot – Bedienung und Benutzerfreundlichkeit (1–10)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 1 | Interaktive, rechteabhängige Hilfe | **Fertig** | `/pulse-hilfe` wird aus dem Command-Tree erzeugt und nach Berechtigungen gefiltert; `tests/test_command_help.py`. |
| 2 | Persönliches Pulse-Profil | **Fertig** | `/profil` zeigt eigene Rolle, Dienst, Wochenzeit, Aufgaben, Tickets und Schulungen privat; `tests/test_profile_command.py`. |
| 3 | Persönliche Übersicht `/pulse` | **Fertig** | `/pulse` zeigt die vollständige persönliche Übersicht (PR #192); `tests/test_pulse_overview_command.py`. |
| 4 | Intelligente Autovervollständigung | **Teilweise** | Discord-Member-Optionen und Ticket-Schnellantwort-Autocomplete existieren; eine durchgängige Vorschlagslogik für Fälle, Kategorien und alle Befehle fehlt. |
| 5 | Kontextbezogene Fehler- und Rechtehilfe | **Fertig** | `pulse_command_errors.py` erklärt fehlende Rechte, Cooldowns, Transformer- und unerwartete Fehler; `tests/test_command_errors.py`. |
| 6 | Interaktive Menüs für häufige Aktionen | **Teilweise** | Buttons, Selects und Modals sind in Ticket-, Moderations-, Dienst- und Voice-Flows vorhanden; es gibt aber kein einheitliches persönliches Aktionsmenü für alle Bereiche. |
| 7 | Persönliche Befehlsfavoriten | **Fertig** | Persistente Slash-Command-Favoriten je Nutzer (PR #194); `pulse_command_favorites.py`, `tests/test_pulse_command_favorites.py`. |
| 8 | Persönliche Benachrichtigungseinstellungen | **Fertig** | Kategorien, Opt-in-DMs, Inbox-Gruppierung und Einstellungen sind in `pulse_notify_prefs.py`; `tests/test_notify_prefs.py`. |
| 9 | Persönliche und serverweite Ruhezeiten | **Fertig** | Persönliche und globale Quiet Hours inklusive Mitternachtswechsel, Zurückhalten und Bündeln; `tests/test_notify_prefs.py`. |
| 10 | Mehrsprachige Bot-Antworten | **Offen** | Deutsche Texte sind fest im Bot und Dashboard hinterlegt; kein vollständiges Übersetzungs-/Locale-System gefunden. |

### 2. Discord-Bot – Moderation und Sicherheit (11–25)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 11 | Gemeinsame Moderations-Fallnummern | **Teilweise** | `pulse_mod_cases.py` und `pulse_ultimate.py` vergeben IDs/Fallnummern; Teamwarnungen, KI-Moderation und Bürger-Moderation sind aber noch getrennte Fachakten. |
| 12 | Durchgängiger Moderationsverlauf | **Teilweise** | `/mod akte`, Falllisten, Warnhistorie und Ultimate-Fallereignisse existieren; ein einheitlicher Verlauf über alle Moderationsarten fehlt. |
| 13 | Versionierte Begründungsvorlagen | **Fertig** | Verwaltbarer, versionierter Vorlagenkatalog (PR #190); `pulse_mod_reason_templates.py`, `tests/test_mod_reason_templates.py`. |
| 14 | Beweismittelverwaltung | **Teilweise** | Bürger-Moderationsfälle speichern Beweisangaben; Ultimate-Fälle unterstützen Uploads und Dateimetadaten. Die Beweisverwaltung ist nicht über alle Falltypen vereinheitlicht. |
| 15 | Formelles Einspruchssystem | **Teilweise** | Moderations-DMs enthalten einen Einspruchsweg/-hinweis (`appeal_text`); ein vollständiger Einreichungs-, Fristen-, Prüfer- und Entscheidungsprozess wurde nicht gefunden. |
| 16 | Eskalationshinweise bei Wiederholungen | **Teilweise** | Warnstufen, Eskalationsvorschau und KI-Moderationsprüfung existieren; ein einheitlicher, erklärbarer Wiederholungs-Hinweis über alle Fallarten fehlt. |
| 17 | AutoMod-Testmodus ohne Sanktionen | **Offen** | Anti-Nuke besitzt Test-/Dry-Run-Modi; für die eigentlichen AutoMod-Nachrichtenfilter wurde kein gleichwertiger allgemeiner Simulationsmodus gefunden. |
| 18 | Ausnahmen je Kanal und Rolle | **Teilweise** | AutoMod berücksichtigt Ausnahmen wie Moderatoren; die gewünschte vollständig konfigurierbare Kanal-/Rollen-Ausnahmematrix ist nicht durchgängig vorhanden. |
| 19 | Konfigurierbare Raid-Alarmstufen | **Teilweise** | `pulse_raid.py` erkennt Join-Wellen, junge Accounts und Spam; getrennte, konfigurierbare Alarmstufen mit Eskalationsablauf fehlen. |
| 20 | Notfall-Lockdown | **Teilweise** | Anti-Nuke kann einen bestätigten, begründeten und reversiblen Lockdown starten/beenden; zeitliche Ablaufsteuerung und mehrstufige Freigabe fehlen. |
| 21 | Sicherheitsübungen und Simulationen | **Teilweise** | Anti-Nuke-Regelsimulation und Replay-Tests existieren (`tests/test_antinuke_control_center.py`, `tests/test_antinuke_replay.py`); ein durchgängiger Übungsmodus für AutoMod, Raid und Lockdown fehlt. |
| 22 | Alarm bei kritischen Rollen-/Kanaländerungen | **Fertig** | Anti-Nuke verarbeitet Role-, Channel-, Permission- und Guild-Update-Ereignisse; Tests prüfen Berechtigungsänderungen und harmlose Änderungen. |
| 23 | Webhook-Überwachung | **Teilweise** | `cogs/antinuke.py` verarbeitet `on_webhooks_update`; ein separat abgesicherter, umfassend getesteter Webhook-Alarm-/Änderungsverlauf fehlt. |
| 24 | Moderationsstatistiken | **Teilweise** | Falllisten, Warnzähler, Audit und Analytics liefern Teilkennzahlen; eine vollständige Moderationsauswertung nach Typ, Bearbeitungszeit und Ergebnis fehlt. |
| 25 | Fehlalarmprüfung und Feedback für AutoMod | **Teilweise** | KI-Moderationsfälle besitzen Review-Aktionen; ein eigener Fehlalarm-/False-Positive-Rückkanal für normale AutoMod-Regeln fehlt. |

### 3. Discord-Bot – Tickets und Support (26–40)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 26 | Intelligente Ticketverteilung | **Teilweise** | Kategorien, Teamrollen, Claiming und Triage existieren; eine automatische kapazitätsbasierte Zuteilung fehlt. |
| 27 | Ticket-SLA je Kategorie | **Teilweise** | SLA-/Wartezeitlogik und konfigurierbare Ticket-Erinnerungen existieren; eine durchgehend auswertbare SLA-Definition je Kategorie ist nicht vollständig nachgewiesen. |
| 28 | Überfälligkeitsalarm | **Teilweise** | Unclaimed-/Antwort-Erinnerungen, Ticket-Triage und SLA-Aktionen existieren; ein vollständiger Eskalationsablauf je Ticketkategorie fehlt. |
| 29 | Ticket-Übergabe mit Pflichtnotiz | **Fertig** | `/ticket uebergeben` verlangt eine Notiz und protokolliert die Übergabe; `tests/test_ticket_extras.py`. |
| 30 | Schnellantworten | **Fertig** | Kategoriebezogene Ticketantworten mit Autocomplete; `tests/test_ticket_extras.py`. |
| 31 | Interne Ticketnotizen | **Fertig** | `/ticket notiz` und `/ticket notizen` speichern/zeigen Teamnotizen getrennt von öffentlichen Nachrichten und `ticket_events`; erneute Rechteprüfung beim Modal-Submit. Backup, personenbezogener Export und Regressionstests: `pulse_ticket_extras.py`, `pulse_db.py`, `pulse_retention.py`, `tests/test_ticket_extras.py`, `tests/test_privacy.py`. |
| 32 | Ähnliche Tickets vorschlagen | **Fertig** | `/ticket aehnliche` vergleicht den aktuellen Kanalverlauf lokal mit maximal 80 archivierten Transkripten derselben Guild; Jaccard-Ähnlichkeit mit Kategoriegewichtung, begrenzte Dateigröße, sichere Pfadprüfung und kein externer KI-Dienst. Regressionsnachweis: `tests/test_ticket_extras.py`. |
| 33 | Tickets miteinander verknüpfen | **Fertig** | Persistente bidirektionale Verknüpfungen gleicher Guild mit `/ticket verknuepfen`, `/ticket verknuepfungen`, `/ticket trennen`; Dubletten-/Selbst-/Cross-Guild-Schutz, interne Notiz, Backup und personenbezogener Export. Tests: `tests/test_ticket_extras.py`, `tests/test_privacy.py`. |
| 34 | Freiwillige Ticket-Zufriedenheit | **Fertig** | Ticket-Rating-Endpunkt und Auswertung vorhanden; `tests/test_ticket_extras.py` prüft Rating-Report. Voice-Support besitzt zusätzlich eigene Bewertungen. |
| 35 | Abgeschlossene Tickets wiedereröffnen | **Teilweise** | Wiederöffnungs-Ereignisse und Dashboard-Aktionsbezeichnungen existieren; ein vollständiger, getesteter Wiedereröffnungsprozess mit Frist und Berechtigungen ist nicht ausreichend belegt. |
| 36 | Ticket-Zusammenfassung | **Teilweise** | `/ticket zusammenfassung` erstellt lokal eine extraktive Übergabe (Kernaussagen, Beteiligte, Umfang) und kann sie als interne Notiz speichern; `pulse_ticket_extras.build_ticket_summary`, `tests/test_ticket_extras.py`. Es fehlen: offene Fragen, erledigte Schritte, nächster Schritt, Dashboard-Ansicht. Befund: Zeilenumbrüche in `cogs/ticket_tools.py` sind doppelt maskiert (`\\n`) und erscheinen in Discord als Text. |
| 37 | Automatische Ticket-Triage | **Fertig** | `pulse_ticket_triage.py` bewertet Wartezeit, Priorität und Zuständigkeit; `tests/test_pulse_ticket_triage.py`. |
| 38 | Ticket-Statistiken | **Fertig** | `/ticketstats`, Ticketcenter, Rating-Report und Analytics stellen Ticketkennzahlen bereit. |
| 39 | Support-Warteschlange | **Fertig** | Voice-Warteschlange mit Position, Status und Übernahme sowie Ticket-Wartestatus sind vorhanden; `tests/test_voice_queue.py`. |
| 40 | Support-Auslastung und Überlastungshinweise | **Teilweise** | Team-, Ticket- und Voice-Zahlen werden angezeigt; ein automatischer, kapazitätsbasierter Lastverteiler samt Überlastungswarnung fehlt. |

### 4. Discord-Bot – Community und BOCHUM RP (41–55)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 41 | Discord-Eventverwaltung mit Anmeldung | **Teilweise** | Der Team-Kalender in `pulse_ultimate.py` kann Termine/Events verwalten; ein nativer Discord-Event- und RSVP-Button-Flow ist nicht vollständig nachgewiesen. |
| 42 | Teilnehmerlisten für Events | **Fertig** | Zu-/Absagen je Kalendertermin in `ultimate_calendar_rsvps` (PR #195); `tests/test_ultimate_event_rsvps.py`. |
| 43 | Event-Erinnerungen | **Teilweise** | Interview-/Recruiting-Erinnerungen und allgemeine Automationen existieren; ein durchgängiger Event-Erinnerungsdienst fehlt. |
| 44 | Umfragen | **Teilweise** | `/ultimate/polls` bietet Single-/Multiple-Choice-Abstimmungen im Dashboard; native Discord-Umfragen und Bot-Commands sind nicht vollständig integriert. |
| 45 | Community-Vorschlagsbox | **Teilweise** | `/ultimate/feedback` nimmt anonyme/namentliche Vorschläge mit Status auf; ein eigener Discord-Einreichungs- und Community-Abstimmungsflow fehlt. |
| 46 | Feedbacksystem | **Teilweise** | Ultimate-Feedback und Ticket-/Voice-Ratings existieren; ein gemeinsamer Community-Feedbackprozess über Discord und Dashboard ist noch nicht vorhanden. |
| 47 | Regelwerk-Assistent mit Quellen | **Teilweise** | Support Assistant und `support_knowledge.md` liefern Wissensantworten; zitierte, versionierte Regelwerkspassagen mit direkten Quellenlinks fehlen. |
| 48 | Freigegebene Fraktionsübersicht | **Fertig** | Fraktionsverwaltung mit offiziellen Fraktionen und öffentlicher, freigabegesteuerter Darstellung; `tests/test_factions.py`, `tests/test_public_site.py`. |
| 49 | Spieler-Meldesystem | **Teilweise** | Ultimate-Fallverwaltung mit Fallnummer, Betreff, Anhängen und Spielerreferenzen existiert; ein klarer, öffentlicher Spieler-Meldeablauf in Discord fehlt. |
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
| 61 | Gespeicherte Filter und Ansichten | **Offen** | Filter in Ticket-/Suchansichten existieren, aber keine benutzerspezifisch gespeicherten Filtersets. |
| 62 | Globale Suche | **Teilweise** | `pulse_suite` stellt eine globale Suche über mehrere TeamOS-Datenbereiche bereit; Vollständigkeit über alle Tickets, Wiki-Versionen, Bewerbungen und Auditdaten ist nicht belegt. |
| 63 | Rollenabhängige Navigation | **Fertig** | Navigation wird nach Rechten gefiltert; `tests/test_dashboard_redesign.py` prüft Berechtigungsfilter und sensitive Menüpunkte. |
| 64 | Mobile Dashboard-Version | **Teilweise** | Responsive Layouts und mobile E2E-Tests existieren; nicht alle Dashboard-Workflows sind vollständig auf Mobilgeräten geprüft. |
| 65 | Einheitliches Designsystem | **Teilweise** | Gemeinsame Design-Tokens und Komponenten (`pulse_design.py`, `static/pulse.css`) sind vorhanden; ältere Seiten sind nicht vollständig vereinheitlicht. |
| 66 | Dark-Mode-Feinschliff | **Teilweise** | Theme-aware Klassen und gemeinsame Farbpalette existieren; konsistente Darstellung jeder Legacy-Seite ist nicht vollständig belegt. |
| 67 | Tastaturbedienung | **Teilweise** | Einzelne zugängliche Navigationselemente/Fokuszustände sind getestet; durchgängige Tastaturbedienung aller Arbeitsabläufe fehlt. |
| 68 | Kontextbezogene Dashboard-Hilfen | **Teilweise** | Fehler- und Rechtehinweise sind vorhanden; eine vollständige kontextuelle Hilfe für komplexe Einstellungen/Formulare fehlt. |
| 69 | Benutzerdefinierte Ansichten | **Offen** | Widget-Sichtbarkeit ist vorhanden, aber keine frei speicherbaren Tabellen-/Dashboard-Ansichten oder Layouts. |
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
| 80 | Kompetenzmatrix | **Offen** | Keine eigenständige Skill-/Kompetenz-Matrix mit Nachweisen und Qualifikationsstufen gefunden. |
| 81 | Team-Auslastung | **Teilweise** | Aufgaben, Tickets, Schichten und Team-Score liefern Belastungsindikatoren; eine einheitliche, aktuelle Arbeitslastansicht fehlt. |
| 82 | Automatische Arbeitslastverteilung | **Offen** | Keine automatische Zuweisung von Aufgaben/Tickets anhand der Auslastung gefunden. |

### 7. Dienstplanung und Aktivität (83–94)

| Nr. | Funktion | Status | Repository-Befund / Beleg |
|---:|---|---|---|
| 83 | Schichtplanung | **Teilweise** | Schichtstart/-pause/-ende und geplante Team-Schichten existieren; Planungs-/Änderungsablauf ist noch nicht durchgängig mit Tests abgesichert. |
| 84 | Schichttausch | **Offen** | Kein Antrag-/Genehmigungsprozess für Schichttausch gefunden. |
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
| 128 | Personalbedarfsprognose | **Offen** | Keine Berechnung des benötigten Personals je Zeitfenster gefunden. |
| 129 | Anomalieerkennung | **Teilweise** | Rule-based Findings zu SLA, Systemzustand und Volumen existieren; keine umfassende Anomalieerkennung über alle Datenbereiche. |
| 130 | Automatische Tagesberichte | **Teilweise** | Team-Report-Automationsschritte und Copilot-Zusammenfassungen existieren; kein fertig vorkonfigurierter, täglicher geprüfter Berichtslauf. |
| 131 | Automatische Wochenberichte | **Teilweise** | Wochenkennzahlen und Reports existieren; automatischer Wochenbericht mit Trendvergleich und Datenquellen fehlt. |
| 132 | Führungskräfte-Assistent | **Teilweise** | `/ultimate/copilot` beantwortet begrenzte Fragen auf Basis lokaler Kennzahlen; keine vollständige Abfrage über alle autorisierten Pulse-Daten. |
| 133 | Wissensassistent mit Quellenangaben | **Teilweise** | Support Assistant nutzt freigegebene Wissensdateien; Quellzitate/Versionen je Antwort fehlen. |
| 134 | Ticket-Zusammenfassungen | **Teilweise** | Lokale Zusammenfassung existiert nur als Discord-Befehl (siehe #36); eine einheitliche Funktion für Discord und Dashboard mit Sachstand, offenen Fragen, Schritten, Beteiligten und nächstem Schritt fehlt. |
| 135 | Bewerbungs-Zusammenfassungen | **Offen** | Keine eigene, überprüfbare Zusammenfassung von Bewerbungsantworten gefunden. |
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
| 164 | Notfallhandbuch | **Teilweise** | Betriebs-, Backup- und Deployment-Dokumentation existiert; ein durchgängiges Incident-Handbuch mit Rollen, Eskalation und Wiederanlauf-Checklisten fehlt. |
| 165 | Admin-Aktionshistorie | **Teilweise** | Suite-Audit und Settings-History protokollieren viele Adminänderungen; vollständige Abdeckung sämtlicher Admin-Aktionen und ein einheitlicher Viewer fehlen. |
| 166 | Sicherer Wartungsmodus | **Fertig** | Globaler Wartungsmodus und Schreibsperren sind vorhanden; E2E prüft abgelehnte Schreibaktionen während Maintenance. |
| 167 | Feature-Schalter | **Fertig** | `pulse_flags`/Admin-Feature-Schalter mit persistenter Aktivierung; `tests/test_dashboard_redesign.py` und Admin-Tests. |
| 168 | Testserver-/Testdatenmodus | **Teilweise** | CI-Fakes, Preview-Server und Workflow-Testmodus existieren; ein konfigurierbarer isolierter Test-Guild-Modus für Admins fehlt. |
| 169 | Release-Übersicht | **Teilweise** | Version und Status werden angezeigt; laufender Commit, Deploymentvergleich, letzter Release und Rollback-Stand sind nicht vollständig zusammengeführt. |
| 170 | Datenintegritätsprüfung | **Fertig** | Read-only Konsistenzdiagnose, Bewerbungsdatenprüfung, Schichthistorie und SQLite-Quick-Check; `tests/test_consistency.py`, `tests/test_application_data_audit.py`, `tests/test_system_checks.py`. |
| 171 | Datenschutz-Anfragen | **Teilweise** | Personenexport, IP-Audit, Retention und KI-Vergessen-Funktion existieren; ein zentraler Auskunft-/Berichtigungs-/Löschantragsworkflow mit Status fehlt. |
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
5. **Noch offen (9):** #10 Mehrsprachigkeit, #17 AutoMod-Testmodus, #61 gespeicherte Filter, #69 benutzerdefinierte Ansichten, #80 Kompetenzmatrix, #82 Arbeitslastverteilung, #84 Schichttausch, #128 Personalbedarfsprognose, #135 Bewerbungs-Zusammenfassungen.
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

1. **Bot/Moderation:** Einspruchsprozess, AutoMod-Dry-Run, Grundvorlagen und False-Positive-Feedback.
2. **Support:** interne Ticketnotizen, Ticketverknüpfungen, Wiedereröffnung und belastbare SLA-Eskalation.
3. **Dashboard:** Command Palette, Favoriten, gespeicherte Filter und Live-Aktivitätsfeed.
4. **Recruiting/Academy:** Zuweisung/Multi-Reviewer, Lernpfade, Zertifikatsablauf und Wissensversionierung.
5. **Gemeinsame Plattform:** einheitliche Fallreferenzen, Eventvertrag, Audit, Inbox und Rechtepolitik.
6. **Betrieb:** vollständiges Systemcockpit, Release-/Commit-Transparenz und Datenschutz-/Aufbewahrungsübersicht.

Jede weitere Umsetzung sollte eine klar abgegrenzte PR mit eigenen Regressionstests, Berechtigungsprüfung und Daten-/Rollback-Betrachtung erhalten.
