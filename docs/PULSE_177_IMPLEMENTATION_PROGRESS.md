# Pulse 177 – Umsetzungsfortschritt

Arbeitsstand der Mission „177 von 177“. Einzelstatus je Funktion: `docs/PULSE_FEATURE_AUDIT_177.md`.
Regeln: kleine Blöcke, je Block ein Feature-Branch ab `origin/main`, Tests, PR, kein Merge und kein Deployment ohne Freigabe.

## Stand

| Status | Anzahl |
|---|---:|
| Fertig | 73 |
| Teilweise | 103 |
| Offen | 1 |

Aktueller Ausgangspunkt: `main` bei `cf7d866` (PR #205–#210 gemergt). Die Zahlen sind nach den sechs neu abgeschlossenen offenen Kernfunktionen aktualisiert.
Korrektur beim Abgleich: #36 und #134 sind nicht offen, sondern teilweise umgesetzt – `/ticket zusammenfassung` existiert bereits.

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

- Branch: feature/ticket-capacity-alerts
- Ergänzt workload_snapshot als reine, begrenzte Auswertung offener Tickets je Bearbeiter und Guild.
- Die Ticket-Zentrale zeigt Führungskräften die acht am stärksten belasteten Bearbeiter, prioritätsgewichtete Last und eine Warnung beim gleichen Kapazitätsgrenzwert, den die optionale automatische Zuweisung verwendet.
- Die Übersicht ist nur für Ticket-Manager/Führungskräfte sichtbar. Keine Änderung der Zuständigkeiten, kein Datenbankschema und keine Discord-Aktion.
- Tests prüfen Guild-Trennung, geschlossene/unzugewiesene Tickets, Prioritätsgewichtung, Limits und Sichtbarkeit.
- PR #211 (Draft). Erster CI-Lauf rot: `test_viewers_do_not_receive_staff_workload_panel` verlangte, dass der Bearbeitername
  nirgends auf der Seite steht – er steht aber regulär in der Ticketliste. Der Test prüft jetzt die Auslastungszahlen selbst,
  zusätzlich: Auslastung wird für Nicht-Berechtigte gar nicht erst berechnet, Tickets fremder Guilds zählen nicht mit.
- Korrektur der Bereichstabelle im Audit: Support hat 11 fertig / 4 teilweise (Summe war bereits richtig, die Zeile nicht).
- Bekannte Abweichung: Alt-Tickets ohne `guild_id` erscheinen in der Liste, zählen aber – wie bei der automatischen Zuweisung –
  nicht in der Auslastung.

## Block 6 – Überfälligkeits-Eskalation und Wiederöffnen (#27, #28, #35)

- Branch: `feature/ticket-escalation-reopen-window` (baut auf Block 5 auf, weil beide dieselben Dateien ändern; Draft-PR #212 gegen `main`)
- Dateien: `pulse_ticket_design.py`, `pulse_ticket_triage.py`, `cogs/tickets.py`, `pulse_db.py`, `webserver.py`,
  `pulse_ticket_builder.py`, `pulse_ticket_center.py`, `tests/test_ticket_escalation.py` (neu), `tests/test_ticket_workflow.py`,
  `tests/test_ticket_center_workload.py`
- Keine neue Route, keine neue Tabelle, keine Migration. Neue Einstellungen in `ticket_system_config`:
  `automation.escalation_minutes` (auch je Kategorie) und `reopen.window_days`; beide Standard 0 = aus, bestehende Server
  verhalten sich unverändert. Neue Schlüssel im vorhandenen Feld `tickets.automation_state`.
- **#28:** Bleibt eine Team-Erinnerung folgenlos, informiert Pulse einmal die Ticket-Führungsebene (Protokollkanal, Inbox,
  Historie, Audit). Die Markierung wird vor dem Discord-Aufruf gespeichert – ein Discord-Fehler führt nie zu Wiederholungen.
- **#35:** Frist für das Wiederöffnen, serverseitig geprüft. Behobener Fehler: Ein wiedergeöffnetes Ticket galt sofort als
  überfällig (Wartezeit ab der ursprünglichen Eröffnung) und bekam nie wieder eine Übernahme-Erinnerung (alter Merker blieb stehen).
- **#27:** war auf `main` bereits umgesetzt und getestet, im Audit aber noch als teilweise geführt; jetzt mit Nachweisen fertig.
- **#26** bleibt teilweise: Die Zuteilung existiert und ist getestet, berücksichtigt aber keine Abmeldungen.
- Einschränkungen: Eskalation setzt eine aktive Erinnerung voraus (ohne Erinnerung keine zweite Stufe); Erinnerungen von vor
  diesem Stand haben keinen Zeitstempel und werden nicht nachträglich eskaliert; die Inbox erreicht höchstens 25 Personen.

## Block 7 – Zuteilung ohne Abgemeldete (#26)

- Branch: `feature/ticket-assignment-absences` (baut auf Block 6 auf; Draft-PR gegen `main`)
- Dateien: `pulse_ticket_distribution.py`, `cogs/tickets.py`, `pulse_ticket_builder.py` (Hinweistext),
  `tests/test_ticket_assignment_absences.py` (neu)
- Die automatische Zuteilung liest `abmeldungen.db` nur lesend und überspringt Teammitglieder, deren Abmeldung heute gilt.
  Keine neue Einstellung, keine Route, keine Tabelle. Ohne Abmelde-Datenbank oder bei Lesefehler verhält sich die Zuteilung wie bisher.
- Unlesbare Datumsangaben zählen als abwesend – ein Ticket an eine abwesende Person ist der schlimmere Fehler.
- Damit ist der Support-Bereich (#26–#40) vollständig: 15 / 0 / 0.

## Prüfstand 11.11.2026

- `main` = `cf7d866`, **nicht geschützt** (GitHub-API: `protected: false`, Issue #121 gilt weiter). Das ist eine
  Repository-Einstellung und lässt sich nicht durch Code beheben.
- PR #211: nach der Testkorrektur alle 13 Prüfungen grün; der Job „Schichthistorie – 20.000 Einträge“ wird übersprungen und
  ist kein Testnachweis.
- `docs/ROUTEN.md` ist auf `main` veraltet (Datei: 411 effektive Routen, `tools/route_map.py` erzeugt 422). Nicht in diesem
  Block aktualisiert, weil er keine Route ändert.

## Nächste Arbeit

1. **Locale-System (#10):** Spracheinstellung und Übersetzungsinfrastruktur; anschließend Kernbefehle und weitere Bot-Antworten systematisch migrieren.
2. **Moderation (#11–#25):** gemeinsame Fallakten, Einsprüche, Beweismittel, Ausnahmen, Raid-Stufen und False-Positive-Rückkanal.
3. Danach die übrigen teilweise bewerteten Punkte bereichsweise, jeweils mit Verhaltenstests und Rechte-/Datenschutzprüfung.
## Hinweise für die Weiterarbeit

- Arbeitsverzeichnis ist ein eigener Git-Worktree; der Haupt-Checkout enthält nicht eingecheckte Nutzeränderungen und bleibt unberührt.
- `docs/ROUTEN.md`, `docs/routen_snapshot.json` und `docs/DATENLANDKARTE.md` werden mit `python tools/route_map.py` und
  `python tools/data_map.py` erzeugt. Sie waren auf `main` veraltet; der große Diff in Block 1 stammt daher.
- Lokale Tests unter Windows: Backup-/Restore-Tests scheitern an Dateisperren (WinError 32) und die Browser-E2E-Tests am
  lokalen Setup – auch auf unverändertem `main`. Maßgeblich ist die CI (Linux).
- `pulse_ultimate.py` und `webserver.py` stehen nicht in der Ruff-Liste (`ruff.toml`); neue Module dort eintragen.
