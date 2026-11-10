# Pulse 3.0 – Umsetzungs-Backlog für Bot, TeamOS und Intelligence

**Status:** Vorschlag / noch nicht implementierte Produkt-Roadmap  
**Basis:** `main` @ `93be3bc8e12d59f2661c828c904d404f3de463d7` · Pulse 7.17.0  
**Ziel:** Discord-Bot, Web-Dashboard und Daten-/Automatisierungsplattform als ein zusammenhängendes TeamOS weiterentwickeln.

> Dieses Dokument ist ein priorisierter Backlog, keine Behauptung, dass die genannten Funktionen bereits umgesetzt sind. Vor jedem Arbeitspaket muss geprüft werden, ob der aktuelle Code die Funktion bereits vollständig oder teilweise bereitstellt. Bereits vorhandene Funktionen werden erweitert und integriert, nicht parallel neu gebaut.

## 1. Verbindliche Leitplanken

1. `CLAUDE.md`, `docs/PULSE_ROADMAP_2_0.md`, `docs/VERBESSERUNGSPLAN.md` und aktuelle Phasenberichte bleiben maßgeblich. Bei Konflikten gilt `CLAUDE.md`.
2. Keine Funktionen, URLs, HTTP-Methoden, Formularfelder, Slash-Command-Namen, `custom_id`-Werte, Discord-IDs, JSON-Schlüssel, Statuswerte, DB-Spalten oder Rechte stillschweigend entfernen oder inkompatibel ändern.
3. Erst Codepfade, aktive Routen, Datenquellen, Rechte und Tests inventarisieren. Alte Handler können zur Laufzeit ersetzt werden; tote oder verdrängte Implementierungen dürfen nicht versehentlich geändert werden.
4. Kleine, abgeschlossene Phasen auf separaten Feature-Branches. Pro Phase: Regressionstests, vollständige relevante CI, frisches Review, Phasenbericht und ausdrücklicher Stopp vor dem nächsten Block.
5. Kein direkter Push auf `main`, kein Merge und kein Deployment ohne die jeweils ausdrückliche Freigabe. Ein grüner CI-Lauf allein ist keine Produktionsfreigabe.
6. Keine Produktivdaten lesen, verändern, löschen, migrieren oder umschalten, sofern es nicht ausdrücklich und separat beauftragt wurde. Kein produktiver Cleanup-/Retention-Lauf.
7. Keine VPS-, Discord-Server-, Rollen-, Kanal-, DNS-, Hosting- oder `.env`-Änderung aus einer Codephase ableiten.
8. Keine verpflichtende kostenpflichtige KI-/Drittanbieter-API. Ohne externe KI muss der Kernbetrieb weiter funktionieren.
9. KI darf zusammenfassen, klassifizieren, suchen und Vorschläge machen, aber nicht autonom verwarnen, befördern, degradieren, kicken oder bannen.
10. Die öffentliche Website bleibt bis zu einer gesonderten rechtlichen Freigabe außerhalb des Funktionsumfangs; interne Team- und Verwaltungsdaten dürfen nicht öffentlich werden.
11. Ressourcenbudget und bestehende Betriebsgrenzen beachten (derzeit 2 GiB Host-RAM; Containerbudget laut Roadmap). Keine unbegrenzten Caches, Queues, Hintergrundtasks oder Datenbestände.
12. Datenmodell- oder Speicheränderungen benötigen eine eigene Datenphase mit Inventur, Backup-Abdeckung, Dry-Run, Paritätsprüfung, Idempotenz, Rollback und separater Freigabe.

## 2. Bereits vorhandene Grundlagen – nicht doppelt bauen

Die aktuelle README und der Code enthalten unter anderem Discord-OAuth und Rollenrechte, Teamakten/Timeline, Warn- und Beförderungsabläufe, Schichtverwaltung und Activity Check, Abmeldungen, Bewerbungen und Recruiting-Bausteine, Tickets und Ticket-Triage, Voice-Support mit Warteschlange, AutoMod, Anti-Nuke, KI-Moderation, Raid-/Honeypot-Schutz, Logs/Audit, Backups/Restore, Trainings/Quiz, Wiki, Kalender, Benachrichtigungen, Analytics, Workflows, Pulse Intelligence, Health-/Systemchecks und Admin-Verwaltung.

Vor allem diese Bereiche sind Integrations- und Ausbaukandidaten. Jede Idee unten erhält vor Umsetzung eine der Kennzeichnungen:
- **Vorhanden:** Im aktuellen Code und durch Verhaltenstests nachgewiesen.
- **Teilweise:** Grundbausteine existieren; genaue Lücke wird benannt.
- **Neu:** Es gibt noch keinen passenden vorhandenen Ablauf.
- **Zurückgestellt:** Datenschutz, Betrieb, Datenmodell oder Produktentscheidung ist noch offen.
- **Verworfen:** Kein nachweisbarer Nutzen oder unvertretbares Risiko.

## 3. Priorisierte Umsetzungsphasen

### Phase 16 – Fundament, Datenintegrität und Betrieb (P0)

**Ziel:** Vor größeren Produkt-Erweiterungen sicherstellen, dass Pulse nachvollziehbar, wiederherstellbar und sicher betrieben werden kann.

Arbeitspakete:
- 16.1 Vollständige Backup-Abdeckungsmatrix aller JSON-Dateien, SQLite-Dateien, Tabellen, Uploads, Anhänge, Transkripte, Konfigurationen und Schlüsselartefakte gegen den tatsächlich verwendeten Komplett-Backup-/Restore-Pfad.
- 16.2 Read-only Datenintegritätsprüfungen für Tickets, Schichten, Abmeldungen, Bewerbungen, Teamakten, Trainings, Referenzen und Statusübergänge; Befunde mit Ursache und sicherem Handlungsvorschlag.
- 16.3 Restore-Vorschau mit Datei-/Tabellen-Diff, Zielprüfung und expliziter Bestätigung; isolierte Restore-Rehearsals und Fehler-/Abbruchtests.
- 16.4 Rechte- und Rollen-Simulation: „Was kann diese Person im Dashboard und Discord tun?“ einschließlich Ranghierarchie, Discord-Berechtigungen, Pulse-Rechten und Default-Deny.
- 16.5 Einheitliche Diagnose für Hintergrundjobs, Task-Queues, API-Latenz, Discord-Gateway, Datenbankzustand und Ressourcenverbrauch.
- 16.6 Verständliche Fehlergruppierung, Fehler-Ringpuffer, Systemereignisse und Benachrichtigung an einen ausdrücklich konfigurierten privaten Betriebskanal.
- 16.7 Release- und Deployment-Transparenz: tatsächlich laufender Commit, App-Version, letzter erfolgreicher Healthcheck und Rollback-Status – ohne selbst zu deployen.
- 16.8 Branch-/CI-Schutz und Deployment-Gates gemäß bestehendem OPS-Backlog überprüfen und manuell erforderliche GitHub-Administration dokumentieren.
- 16.9 Konfigurationsprüfung mit Vorschau und Warnung vor widersprüchlichen oder unsicheren Einstellungen.
- 16.10 Aufbewahrungs- und Datenschutz-Inventar vervollständigen; keine automatische Löschung aktivieren.

**Abnahme:** Nur lesende Diagnose verändert keine Daten; Backup-/Restore-Tests laufen ausschließlich isoliert; Rechte-Simulation stimmt mit echten Route-/Command-Checks überein; keine Geheimnisse in Logs; alle Fehlerpfade sind getestet.

### Phase 17 – Gemeinsame Plattform für Discord und Dashboard (P0/P1)

**Ziel:** Beide Oberflächen arbeiten auf denselben Fachvorgängen und Berechtigungsregeln.

Arbeitspakete:
- 17.1 Gemeinsame Fall-/Vorgangsreferenzen für Tickets, Moderation, Spieler-Meldungen, interne Untersuchungen und Beschwerden; nur dort zusammenführen, wo Zuständigkeit und Datenzugriff gleichartig sind.
- 17.2 Einheitliches Ereignisprotokoll mit Akteur, Ziel, Aktion, Zeitpunkt, Quelle, Ergebnis und Korrelations-ID.
- 17.3 Gemeinsame Benachrichtigungs-Inbox mit persönlichen Präferenzen, Ruhezeiten, Prioritäten und Deduplizierung.
- 17.4 Gemeinsame Aufgaben und Übergaben; Statusänderung in Discord oder Dashboard wird konsistent abgebildet.
- 17.5 Sichere Direktlinks von Discord-Nachrichten auf den zugehörigen, serverseitig autorisierten Dashboard-Vorgang.
- 17.6 Gemeinsame Vorlagenbibliothek für freigegebene Ticketantworten, Moderationsgründe, Team-News und Ankündigungen.
- 17.7 Ereignis-/Domain-Service für ausgewählte Vorgänge, damit Bot und Web nicht dieselbe Geschäftslogik duplizieren.
- 17.8 Idempotenz, Race-Condition-Schutz und Retry-Regeln für Discord-Interaktionen und Dashboard-Aktionen.
- 17.9 Echtzeit-Aktualisierung ausgewählter Dashboard-Bereiche mit begrenzten Verbindungen und sicherem Fallback auf normales Laden.

**Abnahme:** Gleiche Aktion führt über beide Oberflächen zum gleichen fachlichen Ergebnis; Rechte werden serverseitig geprüft; Wiederholungen erzeugen keine Duplikate; bestehende Commands, Routen und Custom IDs bleiben kompatibel.

### Phase 18 – Discord-Bot und Moderation (P1)

**Bedienung**
- 18.1 Hilfesystem und persönliche Übersicht mit rollenabhängigen, echten Command-Tree-Daten und Beispielen ausbauen.
- 18.2 Persönliches Profil für eigene Rolle, Dienst-/Abwesenheitsstatus, Aufgaben, Trainings und Benachrichtigungseinstellungen.
- 18.3 Kontextbezogene Fehlermeldungen mit fehlender Berechtigung oder nächstem erlaubten Schritt.
- 18.4 Autocomplete und interaktive Auswahl für Teammitglieder, Fälle, Tickets und Kategorien.
- 18.5 Persönliche Favoriten und schnell erreichbare häufige Aktionen.
- 18.6 Ruhezeiten, Reminder-Bündelung und individuelle Benachrichtigungskanäle.
- 18.7 Internationalisierung erst nach vollständiger deutscher Terminologie und Tests.

**Moderation und Sicherheit**
- 18.8 Gemeinsame Moderations-Fallnummern und durchgängiger Verlauf.
- 18.9 Versionierte Begründungsvorlagen und sichere Beweismittelreferenzen mit Zugriffsprüfung.
- 18.10 Einspruchsprozess mit Eingang, Frist, unabhängiger Prüfung, Entscheidung und Audit.
- 18.11 Eskalationshinweise bei wiederkehrenden Fällen – ausschließlich Vorschläge, keine automatische Sanktion.
- 18.12 AutoMod-Simulationsmodus / Dry-Run für neue Filter, Ausnahmen und Kanal-/Rollenregeln.
- 18.13 Raid-Alarmstufen und simulierte Sicherheitsübungen für Anti-Nuke/Raid-Schutz.
- 18.14 Notfall-Lockdown nur mit klarer Vorschau, abgestufter Freigabe, zeitlicher Begrenzung und Rücknahmeplan.
- 18.15 Webhook-/Rollen-/Kanalberechtigungsänderungen überwachen und auffällige Änderungen melden.
- 18.16 Fehlalarm-Feedback mit nachvollziehbarer Auswertung; keine selbstlernende automatische Sanktion.

**Community und Support**
- 18.17 Ticketverteilung anhand von Kategorie, Zuständigkeit und transparenten Kapazitätsregeln.
- 18.18 SLA-/Überfälligkeitsanzeige, Pflichtübergaben, interne Notizen, Schnellantworten und Ticket-Verknüpfungen.
- 18.19 Ticket-Zusammenfassungen und Triage als Vorschläge; Menschen bestätigen Einstufungen und Antworten.
- 18.20 Freiwilliges Ticketfeedback und kontrollierte Wiedereröffnung.
- 18.21 Event-Anmeldung, Teilnehmerlisten, Umfragen und Erinnerungen, soweit bestehende Kalender-/Eventmodule nicht genügen.
- 18.22 Community-Vorschläge und Feedback mit Status, Abstimmung und Moderation.
- 18.23 Regelwerk-Assistent mit belegten Antworten aus ausdrücklich freigegebenen und versionierten Dokumenten.
- 18.24 IC-/OOC-Falltypen und Spieler-Meldungen mit klarer Trennung der Zuständigkeiten.
- 18.25 Geplante Ankündigungen und Lesebestätigungen in bestehende Team-News integrieren.

**Abnahme:** Moderationsmaßnahmen bleiben menschlich autorisiert; jeder kritische Schritt hat Rechte-, Hierarchie-, Bestätigungs- und Audit-Tests; Discord-Rate-Limits und Ausfälle werden sauber behandelt.

### Phase 19 – Dashboard und Teamverwaltung (P1)

- 19.1 Personalisierte Startseite mit konfigurierbaren Widgets und rollenabhängigen Standardansichten.
- 19.2 Globale Suche über Teammitglieder, Tickets, Bewerbungen, Aufgaben, Wiki und Audit – nur innerhalb der jeweiligen Berechtigungen.
- 19.3 Command Palette (z. B. Strg+K), Favoriten, gespeicherte Filter und zuletzt verwendete Ansichten.
- 19.4 Einheitliche Komponenten, responsives Layout, Tastaturbedienung, sichtbarer Fokus, Reduced Motion und konsistente Cursor-/Hover-Zustände.
- 19.5 Live-Aktivitätsfeed und gezielte Echtzeit-Updates ohne unnötige Polling-Last.
- 19.6 Teamakte und Timeline zu einer nachvollziehbaren, zugriffsgeschützten Übersicht konsolidieren.
- 19.7 Kompetenzmatrix und Team-Auslastung als erklärbare Kennzahlen, nicht als intransparente Leistungsbewertung.
- 19.8 Onboarding-/Offboarding-Checklisten mit expliziten Freigaben für Rollenänderungen.
- 19.9 Schichtplanung, Tauschanträge, Dienstabdeckung, Übergabeprotokolle und Abwesenheitskalender – bestehende Dienst- und Abmeldesysteme zuerst inventarisieren.
- 19.10 Wochen-/Monatsberichte mit nachvollziehbaren Kennzahlendefinitionen und Datenquellen.
- 19.11 Recruiting-Pipeline mit Zuweisung, mehreren Prüfern, Interview-Bewertungsbogen, Kalender, Erinnerungen und Einstellungs-Checkliste.
- 19.12 Bewerbungs-Dublettenhinweise nur als prüfbare Indizien; keine automatische Ablehnung und keine zusätzliche personenbezogene Profilbildung.
- 19.13 Team-News, Lesebestätigungen, Aufgaben und globale Inbox konsistent zusammenführen.

**Abnahme:** Keine Rechteausweitung; mobile und Desktop-E2E-Tests; alle Ansichten verwenden die aktiven Route-Handler; bestehende Formulare und Statuswerte bleiben kompatibel.

### Phase 20 – Academy und Wissensmanagement (P2)

- 20.1 Interne Lernplattform mit Lernpfaden für Support, Moderation, Recruiting und Leitung.
- 20.2 Quiz- und Fallbeispiel-Editor mit Versionierung und nachvollziehbaren Bewertungskriterien.
- 20.3 Schulungszuweisung, Fortschritt, Wiederholungen und Ablauf-/Auffrischungshinweise.
- 20.4 Zertifikate oder Qualifikationsnachweise mit Aussteller, Gültigkeit und Widerruf.
- 20.5 Wiki mit Kategorien, Volltextsuche, Berechtigungen und Versionshistorie.
- 20.6 Dienstanweisungen mit bestätigter Kenntnisnahme und nachvollziehbarer Version.
- 20.7 Schulungsfeedback und Wirksamkeitsauswertung ohne personenbezogene Überwachung.

**Abnahme:** Versionshistorie und Rechte sind getestet; falsche oder veraltete Lerninhalte können zurückgezogen werden; personenbezogene Lerninformationen sind geschützt.

### Phase 21 – Pulse Intelligence und Automatisierungen (P2)

- 21.1 Regelbasierte Tages-/Wochenberichte als Entwurf mit Datenquellen und Zeitfenster.
- 21.2 Ticketlast- und Personalabdeckungs-Prognosen mit Unsicherheiten und ausreichender Datenbasis.
- 21.3 Anomaliehinweise für Systemzustand, Ticketvolumen, Schichtdauer und Jobfehler.
- 21.4 Führungskräfte-Assistent für lesende Fragen auf Basis autorisierter Pulse-Daten.
- 21.5 Wissensassistent mit Quellenangaben aus freigegebenen Regelwerken und Wiki-Artikeln.
- 21.6 Zusammenfassungen für Tickets, Bewerbungen und Schichtübergaben als überprüfbare Entwürfe.
- 21.7 Was-wäre-wenn-Simulationen für Ticketlast, Personalausfall, Ressourcen und Workflow-Konfigurationen.
- 21.8 Visueller Workflow-Editor mit Triggern, Bedingungen, Aktionen und Vorlagen.
- 21.9 Workflow-Sandbox, Versionshistorie, Dry-Run, Freigaben, Idempotenz, Retry-/Abbruchregeln und Ausführungsprotokoll.
- 21.10 Kritische Aktionen brauchen menschliche Freigabe; Workflows erhalten keine höheren Rechte als der auslösende Akteur und die explizit erteilte Freigabe.
- 21.11 Lokale/regelorientierte Funktionen bleiben ohne KI-Anbieter verfügbar; optionale KI ist budgetiert, abschaltbar und datensparsam.

**Abnahme:** Kein stiller Schreibzugriff; jede Empfehlung zeigt Datenbasis und Unsicherheit; Workflow-Simulation führt keine Aktionen aus; Berechtigungen werden bei Ausführung erneut geprüft.

### Phase 22 – BOCHUM-RP-spezifische Leitstelle und Community-Prozesse (P2/P3)

- 22.1 BOCHUM-RP-Leitstand mit Teamverfügbarkeit, offenen Supportfällen, Eskalationen, Kalender und Betriebsstatus.
- 22.2 Abteilungsverwaltung mit Verantwortlichen, Zuständigkeiten, Aufgaben, Schulungen und Statistiken.
- 22.3 Vorgangsverwaltung für größere Teamfälle mit Beteiligten, Belegen, Status, interner Kommunikation und Abschlussbericht.
- 22.4 Academy mit BOCHUM-RP-Lernpfaden und Rollenqualifikationen.
- 22.5 Team-Leitstand mit tatsächlichem Dienststatus und verfügbaren Supportkapazitäten.
- 22.6 Ideen-/Abstimmungsportal und Zielplanung für Abteilungen.
- 22.7 IC-/OOC-Meldungen, Einsprüche und Spielerkommunikation in getrennten, nachvollziehbaren Prozessen.
- 22.8 Öffentliche oder externe Darstellung erst nach gesonderter rechtlicher, datenschutzrechtlicher und Betreiberfreigabe.

**Abnahme:** Der Leitstand ist eine integrierte Ansicht vorhandener Daten statt eines zweiten Datenbestands; keine nicht freigegebenen Team- oder Spielerdaten werden veröffentlicht.

## 4. Vorgeschlagene Reihenfolge und Abhängigkeiten

| Reihenfolge | Block | Abhängigkeit | Priorität |
|---|---|---|---|
| 1 | Phase 16 – Fundament und Betrieb | Bestehende Phase-15-Nachweise aktualisieren | P0 |
| 2 | Phase 17 – Gemeinsame Plattform | stabile Fach- und Rechteverträge | P0/P1 |
| 3 | Phase 18 – Bot/Moderation/Support | gemeinsame Fälle, Ereignisse und Rechte | P1 |
| 4 | Phase 19 – Dashboard/Team | gemeinsame APIs und Datenverträge | P1 |
| 5 | Phase 20 – Academy/Wiki | Team-/Rollen-/Trainingsinventur | P2 |
| 6 | Phase 21 – Intelligence/Workflows | verlässliche Daten und Rechte | P2 |
| 7 | Phase 22 – Leitstelle/BOCHUM-RP | integrierte Module und Freigaben | P2/P3 |

Die Nummerierung beschreibt Planungsblöcke und ist keine Zusage, dass jede Idee umgesetzt werden muss. Jede Phase beginnt mit einer aktuellen Codeinventur und endet mit einem Bericht samt getesteten Ergebnissen.

## 5. Definition of Done für jede Phase

- Scope enthält konkrete bestehende Lücke, Nutzen, Nicht-Ziele und betroffene Module.
- Aktive Routes, Slash-Commands, Interaktions-IDs, Datenquellen, Schreibpfade und Rechte wurden geprüft.
- Tests verwenden Fakes und temporäre Daten; keine Verbindung zu echtem Discord und keine produktiven Datenmutationen.
- Regressionstests belegen unverändertes Verhalten der Alt-Funktionen.
- Sicherheits-, Datenschutz-, Performance-, Accessibility- und Fehlerpfade sind entsprechend dem Scope geprüft.
- Relevante vollständige CI-Prüfungen sind grün; übersprungene oder nicht ausgeführte Prüfungen werden ausdrücklich genannt.
- Frisches Review prüft Korrektheit, Berechtigungen, Datenverlust, Rückwärtskompatibilität und fehlende Tests.
- Dokumentation, CHANGELOG und Phasenbericht stimmen mit dem tatsächlich umgesetzten Code überein.
- PR bleibt bis zur gesonderten Freigabe offen; kein Deployment durch diese Roadmap.

## 6. Erste konkrete nächste Schritte

1. Phase-16-Read-only-Baseline gegen den tatsächlichen HEAD `93be3bc` erstellen und ältere Roadmap-Aussagen aktualisieren, ohne sie ungeprüft als aktuellen Befund zu übernehmen.
2. Bestehende offene Arbeit zu Datenintegrität, Bewerbungsdaten, Legal/Retention und main-Branch-Schutz zuerst berücksichtigen.
3. Eine Statusmatrix für alle Arbeitspakete erstellen: vorhanden / teilweise / neu / zurückgestellt / verworfen.
4. Erst danach das kleinste P0-Arbeitspaket auswählen und in einem eigenen Feature-Branch implementieren.

---
**Wichtig:** Dieses Dokument selbst implementiert keine Bot- oder Dashboard-Funktionen, ändert keine Laufzeitkonfiguration und führt keine Datenmigration aus.
