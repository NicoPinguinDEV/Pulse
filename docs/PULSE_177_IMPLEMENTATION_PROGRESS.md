# Pulse 177 – Umsetzungsfortschritt

Arbeitsstand der Mission „177 von 177“. Einzelstatus je Funktion: `docs/PULSE_FEATURE_AUDIT_177.md`.
Regeln: kleine Blöcke, je Block ein Feature-Branch ab `main`, Verhaltenstests, Rechte-/Datenschutzprüfung, PR und CI. Keine Produktivdaten, Discord-Server oder VPS-Aktionen im Rahmen dieser Arbeit.

## Stand auf main

| Status | Anzahl |
|---|---:|
| Fertig | 87 |
| Teilweise | 90 |
| Offen | 0 |

**Aktueller Ausgangspunkt:** `main` @ `6c9ff85` (PR #234). PRs #214–#234 sind gemergt bzw. geschlossen (#230). Die Einzelzeilen wurden am 13.11.2026 gegen den Code abgeglichen; Kopfzeile, Gesamt- und Bereichsübersicht stimmen jetzt mit den 177 Zeilen überein (86 + 91 + 0 = 177).

## Zuletzt abgeschlossene Implementierungsblöcke

- **PR #214 – Locale-Grundlage (#10):** per Nutzer gespeicherte Sprache Deutsch/Englisch/Auto, Locale-Auflösung und Übersetzungsbausteine; `/profil` und `/pulse-hilfe` verwenden die Übersetzungen. Weitere Commands, Modals und Dashboard-Texte bleiben Arbeit.
- **PR #215 – AutoMod-Fehlalarmfeedback (#25):** echte AutoMod-Ereignisse können mit Begründung geprüft und als Fehlalarm markiert werden; persistente Guild-Isolation, Audit und private Moderationsbefehle. Keine automatische Sanktionsrücknahme oder Regeländerung.
- **PR #216 – Ticketreferenz-Autocomplete (#4):** verknüpfte Ticketreferenzen werden bei der passenden Eingabe vorgeschlagen; gleiche Guild und Rechte werden geprüft. Die allgemeine Autocomplete-Abdeckung bleibt teilweise.
- **PR #217 – Community-Vorschläge und Feedback (#45/#46):** Discord-Modal, anonyme/namentliche Einreichung, Dashboard-Prüfung, Freigabe, Abstimmung und Ergebnisübersicht; Vorschläge und allgemeines Feedback werden getrennt gehalten.
- **PR #218 – Notfallhandbuch (Issue #164):** Incident-Rollen, Schweregrade, Erstreaktion, sichere Restore-/Rollback-Abfolge und Regressionstests.
- **PR #219 – Spielermeldungen (#49):** strukturierte Discord-Meldung, optionale Belege, private Statusabfrage, Dashboard-Bearbeitung und personenbezogener Export für die relevanten Rollen.

Alle sechs PRs wurden gemergt. Für die Feature-PRs #215, #217 und #219 liefen beide erforderlichen Qualitäts-/Sicherheitsworkflows erfolgreich; #217 und #219 erreichten jeweils zusätzlich die vollständige Regression- und Coverage-Prüfung. Diese CI-Ergebnisse ersetzen keine manuelle Abnahme auf dem Live-Discord-Server.

## Block 1 – Workflow-System (#144, #145, #148, #149)

- Branch: `feature/workflow-versioning-retries-approvals` · PR #202 · CI vollständig grün (Commit `b355de3`)
- Dateien: `pulse_workflows.py` (neu), `pulse_automation_rules.py`, `pulse_ultimate.py`, `tests/test_workflow_engine.py` (neu),
  `docs/ROUTEN.md`, `docs/routen_snapshot.json`, `docs/DATENLANDKARTE.md`, `CHANGELOG.md`, `ruff.toml`
- Neue Tabellen in `pulse.db`: `ultimate_automation_versions`, `ultimate_automation_retries`, `ultimate_automation_approvals`
  (wie alle `ultimate_*`-Tabellen im Komplett-Backup der Datei enthalten, nicht im JSON-Export).
- Neue Routen (alle nur für Führungskräfte, Sicherheits-Automationen nur für Administratoren):
  `GET /ultimate/automations/{id}/versions`, `POST …/versions/{version}/restore`,
  `POST /ultimate/automations/approvals/{id}/decide|cancel`, `POST /ultimate/automations/retries/{id}/resume|cancel`
- Entscheidungen:
  - Wiederholungen sind je Automation abschaltbar und standardmäßig aus – bestehende Automationen verhalten sich unverändert.
  - Ein Schritt wird vor der Ausführung als „läuft“ gespeichert. Nach einem Absturz wird er nicht automatisch wiederholt
    (höchstens einmal), sondern wartet in der Dead-Letter-Liste auf eine Person.
  - Der Notfall-Lockdown wird nie automatisch wiederholt.
  - Wiederherstellen einer Version pausiert die Automation.
- Bekannte Einschränkungen: Freigabe nur im Dashboard (kein Discord-Button); Freigabe gilt für den ganzen Lauf;
  Legacy-JSON-Automationen ohne Schrittliste werden nicht wiederholt.

## Block 2 – Lokale Zusammenfassungen (#36, #134, #135)

- Branch: `feature/local-summaries` (baut auf Block 1 auf, weil beide dieselben Audit-Dateien ändern; PR #203 gegen `main`)
- Dateien: `pulse_summaries.py` (neu), `cogs/ticket_tools.py`, `pulse_pro.py`, `webserver.py`, `tests/test_summaries.py` (neu)
- Neue Route: `GET /ticket/{ticket_id}/summary` (nur `can_manage_tickets`)
- Keine neue Tabelle, keine Datenmigration. Zusammenfassungen werden nicht gespeichert, außer jemand legt sie bewusst als interne Ticketnotiz ab.
- Behobener Fehler: In `cogs/ticket_tools.py` standen Zeilenumbrüche doppelt maskiert; Discord zeigte sie als Text. Mit Regressionstest.
- Einschränkungen: letzte 100 Textnachrichten; Bewerbungsübersicht nur im Dashboard.

## Block 3 – Gespeicherte Filter (#61)

- Branch: `feature/saved-views` (baut auf Block 2 auf; PR gegen `main`)
- Dateien: `pulse_views.py` (neu), `pulse_db.py`, `pulse_ticket_center.py`, `pulse_ultimate.py`, `webserver.py`,
  `tests/test_saved_views.py` (neu), `tests/test_permission_sweep.py`
- Neue Tabelle `dashboard_saved_views` in `pulse.db`, in `pulse_db.TABLES` (JSON-Backup und Restore getestet), im Personenexport über `user_id`.
- Neue Routen: `POST /me/views/save`, `POST /me/views/{view_id}/delete` (persönliche Aktionen, nur eigene Ansichten).
- Eine Ansicht ist nur ein Link auf eine Seite aus einer festen Liste; die Rechteprüfung der Seite bleibt maßgeblich.
- `/ultimate/team` filtert jetzt serverseitig nach Name und Status.

## Block 4 – Kernfunktionen 17, 69, 80, 82, 84 und 128 (PR #205–#210)

Alle sechs zuvor offenen Funktionen wurden in einzelnen Branches umgesetzt, mit Verhaltenstests ergänzt und in main gemergt:

- **PR #205 / #17 – AutoMod-Testmodus:** Live-Nachrichten werden nur simuliert; keine Sanktion, kein Löschen und keine DM. Getrennte Testtreffer und Regressionstests.
- **PR #206 / #69 – benutzerdefinierte Ansichten:** Bis zu acht persönliche „Mein Pulse“-Layouts mit Widget-Sichtbarkeit, Reihenfolge und 1-/2-Spalten-Anordnung.
- **PR #207 / #80 – Kompetenzmatrix:** Rollenbezogene Schulungsnachweise und Qualifikationsstufen; nur Führungskräfte, keine Selbsteinstufung.
- **PR #208 / #82 – Arbeitslastverteilung:** Unzugewiesene Aufgaben werden anhand gewichteter Aufgaben- und Ticketlast verteilt; bestehende Zuweisungen bleiben erhalten.
- **PR #209 / #84 – Schichttausch:** Antrag, Peer-Bestätigung und Leitungsfreigabe; atomare Konfliktprüfung und Datenschutzexport.
- **PR #210 / #128 – Personalbedarfsprognose:** Sieben-Tage-Planungsansicht auf Basis historischer Ticket-Eingänge und bestehender Schichten.

## Block 5 – Support-Kapazität (#40)

- Branch: feature/ticket-capacity-alerts · PR #211 · am 11.11.2026 gemergt.
- Die Ticket-Zentrale zeigt Führungskräften offene Tickets je Bearbeiter, prioritätsgewichtete Last und Warnungen am konfigurierten Kapazitätslimit.
- Verhaltenstests sichern Guild-Trennung, Prioritätsgewichtung, Manager-Sichtbarkeit und Ausschluss geschlossener/unzugewiesener Tickets.
- Keine neue Tabelle, keine Migration, keine automatische Zuständigkeitsänderung.

## Block 6 – Überfälligkeits-Eskalation und Wiederöffnen (#27, #28, #35)

- Branch: feature/ticket-escalation-reopen-window · PR #212 · am 11.11.2026 gemergt.
- #28: Zweite Eskalationsstufe an die Ticket-Führung, wenn eine Erinnerung folgenlos bleibt; standardmäßig aus, einmalig, mit Inbox, Historie und Audit.
- #35: Konfigurierbares Wiederöffnungsfenster, serverseitig geprüft; nach Wiederöffnung beginnt die Wartezeit für Erinnerungen und Triage neu.
- #27 war bereits auf main umgesetzt und durch die vorhandenen Kategorie-SLA-Tests belegt.
- Änderungen bleiben standardmäßig deaktiviert und benötigen Admin-Konfiguration; es gibt keine automatische Sanktion.

## Block 7 – Zuteilung ohne Abgemeldete (#26)

- Branch: feature/ticket-assignment-absences · PR #213 · am 11.11.2026 in main gemergt (Merge-Commit c443528).
- Die optionale automatische Zuteilung liest abmeldungen.db nur lesend und überspringt Personen, deren Abmeldung heute gilt (Europe/Berlin).
- Keine neue Einstellung, Route, Tabelle oder Migration. Fehlt die Abmeldungsdatenbank oder ist sie nicht lesbar, bleibt das bisherige sichere Verhalten erhalten.
- 12 Verhaltenstests decken Tagesgrenzen, Altformate, Guild-Trennung, ungültige Daten, fehlende/beschädigte DB und fehlenden Schreibzugriff ab.
- Damit ist der Support-Bereich #26–#40 auf main vollständig: 15 / 0 / 0.

## Block 8 – Notfallhandbuch (#164)

- Branch: feature/pulse-incident-runbook-164 (dieser Branch).
- Neues Handbuch: docs/PULSE_NOTFALLHANDBUCH.md.
- Enthält Rollen/Verantwortlichkeiten, Schweregrade P1–P4, Eskalationsregeln, 15-Minuten-Erstreaktion, Incident-Protokollvorlage, lesende Diagnosebefehle, Healthcheck-Anforderungen, sichere Restore-/Rollback-Abfolge, Credential-/Datenschutzvorfälle, Wiederanlauf- und Abschlusschecklisten.
- Neue Regressionstests: tests/test_incident_runbook.py (sieben Tests für Rollen/Eskalation, Erstreaktion, korrekte VPS-Kommandos, Healthcheck-Flags, Restore-Freigaben, Secret-Schutz und Abschluss).
- ruff.toml nimmt die Testdatei in die konservative Lint-Liste auf.
- Audit #164 wird in diesem Branch auf Fertig gesetzt. Der Branch-Zähler ist 74 fertig / 102 teilweise / 1 offen; main bleibt bis zum Merge bei 73 / 103 / 1.
- Keine Änderung an Laufzeitdaten, Discord-Produktivserver, VPS, Secrets oder Deployment-Konfiguration.

## Block 10 – Datenschutzanfragen (#171)

- Branch: `feature/privacy-requests-171` ab `main` @ `f0b9e7c` (unabhängig von Block 9 / PR #232; beide ändern nur die Zählerzeilen dieser Dokumente – beim zweiten Merge Zähler addieren).
- Dateien: `pulse_privacy_requests.py` (neu, Fachlogik), `pulse_privacy_requests_web.py` (neu, Dashboard), `cogs/privacy_requests.py` (neu, Discord), `pulse_db.py` (zwei Tabellen + `TABLES`), `pulse_retention.py` (Verlauf im Personenexport), `pulse_admin.py` (Übersichtskarte auf `/admin/privacy`), `pulse_command_help.py`, `webserver.py` (Registrierung), `tests/test_privacy_requests.py` (neu, 18 Tests), `ruff.toml`, generierte Landkarten.
- Neue Tabellen in `pulse.db`: `privacy_requests`, `privacy_request_events` – im JSON-Backup (`pulse_db.TABLES`, Roundtrip getestet), im Komplett-Backup (ganze Datei) und im Personenexport (Anfragen über `user_id`, Verlauf über die eigenen Anfrage-IDs).
- Neue Routen (alle nur Administratoren, jede Änderung im Audit-Log): `GET /admin/privacy/requests`, `POST …/create`, `GET …/{id}` (optional `?scan=1` für die lesende Datenbestandsprüfung), `POST …/{id}/status|assign|extend|note`.
- Neue Discord-Befehle: `/datenschutz-anfrage` (nur für sich selbst, ephemer) und `/datenschutz-status` (nur eigene Anfragen); Deutsch/Englisch über `pulse_i18n.get_locale`.
- Entscheidungen: Frist 30 Tage ab Eingang, einmal bis 60 Tage mit Begründung verlängerbar; Überfälligkeit wird berechnet, nicht gespeichert; Abschluss/Ablehnung nur mit dokumentiertem Ergebnis; je Person und Art eine offene Anfrage, höchstens drei offene; Zuständig nur Serverinhaber/Discord-Administratoren; keine automatische Änderung oder Löschung von Daten.
- Einschränkungen: Texte des neuen Cogs liegen lokal im Cog (keine Änderung an `pulse_i18n.py`, um Konflikte mit PR #222 zu vermeiden); keine Identitätsprüfung über Discord hinaus – die Leitung dokumentiert sie bei Bedarf als Notiz.
## Block 9 – Moderationsstatistik (#24)

- Branch: `feature/moderation-statistics-24` ab `main` @ `f0b9e7c`.
- Bestandsaufnahme 12.11.2026: PR #221–#228 (Features #15, #18, #19, #23, #43, #52, #55, #57) und #229/#230 (Dependabot) offen; diese Funktionen werden hier nicht parallel bearbeitet.
- Dateien: `pulse_mod_stats.py` (neu), `webserver.py` (zwei neue Routen hinter der Fallakte), `pulse_admin.py` (Navigation), `tests/test_mod_stats.py` (neu), `ruff.toml`, generierte Landkarten.
- Neue Routen: `GET /mod-cases/stats`, `GET /mod-cases/stats.csv` – gleiche Rechteprüfung wie `/mod-cases` (Admin oder Kick-/Ban-Moderationsrecht), CSV-Export wird auditiert.
- Keine neue Tabelle, keine Migration, keine Schreibzugriffe. Quellen: `mod_cases`, `events` (AutoMod-Maßnahme/-Testtreffer), `automod_false_positive_feedback`.
- Entscheidungen: Quoten ohne Datenbasis werden als „–“ angezeigt statt 0 %; Testtreffer und `simulation`-Ereignisse zählen nie als Maßnahme; Zeilen fremder Guilds oder mit unlesbaren Zeitstempeln werden ignoriert; je Quelle höchstens 20 000 Zeilen (RAM-Budget).
- Einschränkungen: Einspruchskennzahlen erst nach Merge von PR #225; Teamwarnungen und `/ultimate/cases` sind getrennte Fachakten (#11/#12) und nicht Teil dieser Bürger-Moderationsauswertung; kein Discord-Command (vermeidet Konflikt mit PR #225 in `cogs/mod_cases.py`).

## Block 11 – Spieler-Selbstservice (#55)

- Branch: `feature/self-service-rsvp-55`. Baut auf `audit/177-reconciliation` (PR #235) auf, weil beide dieselben Zählerzeilen ändern.
- Dateien: `pulse_ultimate.py`, `cogs/pulse_pro.py`, `tests/test_player_self_service.py`, generierte Karten (`docs/ROUTEN.md`, `docs/JOBS_UND_RECHTE.md`, nur Zeilennummern), `CHANGELOG.md`.
- `pulse_ultimate.set_calendar_rsvp()` übernimmt die Prüf- und Schreiblogik der Dashboard-Route `POST /ultimate/calendar/{id}/rsvp`. Die Route ruft sie auf; HTTP-Codes (400/404) und Verhalten bleiben gleich. `RSVP_EVENT_KINDS` ersetzt zwei doppelte Literale.
- Neue Discord-Befehle (alle privat):
  - `/event-rueckmeldung`: Autovervollständigung nur für kommende RSVP-Termine. Rechte wie im Dashboard (`can_view_dashboard` oder Admin), sonst kein Schreibzugriff und keine Vorschläge. Nur für Termine, die noch nicht begonnen haben. Audit über `pulse_ultimate.audit` mit dem Zusatz „(Discord)“.
  - `/selbstservice`: Tickets, Termine und Benachrichtigungen; jeder Teil fällt einzeln aus.
- Keine neue Tabelle, keine Migration, keine neue Route, kein neuer Hintergrundjob. Backup und Export sind unverändert, weil nur vorhandene Tabellen beschrieben werden.
- Ergebnis: #55 → Fertig (87 / 90 / 0).

## Prüfstand 13.11.2026 – Abgleich nach Merge-Welle (Branch `audit/177-reconciliation`)

- Referenz: `main` @ `6c9ff85`; beide Pflicht-Workflows auf diesem Commit erfolgreich.
- Widerspruch behoben: Kopfzeile nannte 78/99, Gesamt- und Bereichsübersicht 80/97; die Einzelzeilen ergaben 80/97, berücksichtigten aber PR #221–#228 noch nicht.
- Gegen Code und Tests geprüft und auf **Fertig** gesetzt: #15 (PR #225), #18 (PR #223), #19 (PR #224), #23 (PR #221), #43 (PR #226), #52 (PR #228).
- Bleiben **Teilweise**: #55 (PR #222 liefert nur lesende Selbstservice-Ansichten; Aktionen und gebündelte Übersicht fehlen), #57 (Reihenfolge/Drag-and-drop vorhanden; Widget-Größen und frei definierbare Widgets fehlen).
- Zeilen #24 und #171 verweisen jetzt auf die gemergten PRs #232/#233 statt auf Branches.
- Lokal unter Windows ausgeführt: 135 Tests der betroffenen Module, davon 2 Fehler in `tests/test_privacy.py` (`RetentionTests`) durch `WinError 32` beim Aufräumen der temporären `pulse.db` – bekannte Windows-Dateisperre, auf Linux-CI grün. Die Log-Zeile `AttributeError … 'msg'` bei `GET /ultimate/calendar` stammt aus dem Redirect-Folgen im RSVP-Test mit einem Minimal-Mock und betrifft keinen Produktivpfad.
- Ergebnis: **86 fertig / 91 teilweise / 0 offen**.

## Prüfstand 12.11.2026 – Merge-Welle #221–#233

- Gemergt: #221 (Webhook-Überwachung, #23), #222 (Spieler-Selbstservice, #55), #223 (AutoMod-Ausnahmen, #18), #224 (Raid-Alarmstufen, #19), #225 (Einsprüche, #15), #226 (Event-Erinnerungen, #43), #227 (Widget-Anordnung, #57), #228 (Ankündigungsplanung, #52), #229 (fastapi 0.162.2 inkl. Lock-Hashes), #231 (kaputte Tests auf main, Compose-Log-Race), #232 (#24), #233 (#171).
- Nicht gemergt: #230 (davey 0.2.0) – inkompatibel mit discord.py 2.7.1 (DAVE-Transitions), Begründung im PR.
- Nach der Merge-Welle: `delivery_loop` aus #228 mit `@tracked` versehen, Routenkarte/Jobs-Liste neu erzeugt, Zähler auf 80 / 97 korrigiert.
- Erledigt am 13.11.2026 (siehe „Prüfstand 13.11.2026“).

## Prüfstand 11.11.2026 – nach PR #214 bis #219

- `main` = `75555c79f942de700ad4a48c53d43fa8fce591b5`.
- PR #214, #215, #216, #217, #218 und #219 sind geschlossen und gemergt.
- Die Auditdatei wurde gegen diesen Main-Stand aktualisiert: 78 fertig / 99 teilweise / 0 offen.
- #10 bleibt teilweise: Locale-Grundlage vorhanden, noch nicht alle Antworttexte lokalisiert.
- #4 bleibt teilweise: Ticketreferenz-, Kategorie- und Prioritäts-Autocomplete ergänzt; weitere passende Befehle müssen folgen.
- #25, #45, #46 und #49 sind durch die gemergten Implementierungen und Tests fertig.
- Keine Änderungen an VPS, Live-Discord oder Produktivdaten.

## Nächste Arbeitspakete

1. **#10 und #4 vervollständigen:** weitere häufig verwendete Bot-Antworten und Modals lokalisieren; Autocomplete für die restlichen passenden Team-, Ticket-, Fall- und Kategorieparameter ergänzen.
2. **Moderation #11, #12, #14, #16, #20, #21:** gemeinsame Fall-IDs/-Timeline über alle Fachakten, einheitliche Beweismittel, Wiederholungshinweise, Lockdown mit Ablaufzeit/Freigabe und Übungsmodus.
3. **Community #41–#55 (offen: 8):** native Discord-Events, Discord-Umfragen, zitierter Regelwerk-Assistent, IC/OOC-Datentrennung, Meetings, Lesebestätigungen, Social-Filter und Selbstservice-Aktionen (#55).
4. **Dashboard/Team/Recruiting/Academy #56–#115:** Live-Feed und Suche, volle Widgetanordnung, 360°-Akte, Kompetenz-/Entwicklungsprozesse, Schichtabdeckung, Recruiting-Prüferpipeline, Lernpfade und versioniertes Wiki.
5. **Integration/Intelligence/Workflow/Betrieb #116–#177:** gemeinsame Event-/Policy-/Auditverträge, sichere Prognosen, Workflow-Abhängigkeiten, zentrale Betriebskonsole, vollständige Restore-/Release-/Datenschutzübersicht und BOCHUM-RP-Leitstelle.

Jedes Paket erhält eine eigene Feature-Branch/PR, gezielte Regressionstests, Rechte-/Guild-Isolation, Datenschutz-/Backupprüfung und CI. Eine Funktion wird erst nach Nachweis als „Fertig“ markiert.
## Hinweise für die Weiterarbeit

- Arbeitsverzeichnis ist ein eigener Git-Worktree; der Haupt-Checkout enthält nicht eingecheckte Nutzeränderungen und bleibt unberührt.
- `docs/ROUTEN.md`, `docs/routen_snapshot.json` und `docs/DATENLANDKARTE.md` werden mit `python tools/route_map.py` und
  `python tools/data_map.py` erzeugt. Sie waren auf `main` veraltet; der große Diff in Block 1 stammt daher.
- Lokale Tests unter Windows: Backup-/Restore-Tests scheitern an Dateisperren (WinError 32) und die Browser-E2E-Tests am
  lokalen Setup – auch auf unverändertem `main`. Maßgeblich ist die CI (Linux).
- `pulse_ultimate.py` und `webserver.py` stehen nicht in der Ruff-Liste (`ruff.toml`); neue Module dort eintragen.
