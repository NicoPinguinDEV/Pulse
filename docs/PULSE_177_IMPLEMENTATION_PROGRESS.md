# Pulse 177 – Umsetzungsfortschritt

Arbeitsstand der Mission „177 von 177“. Einzelstatus je Funktion: `docs/PULSE_FEATURE_AUDIT_177.md`.
Regeln: kleine Blöcke, je Block ein Feature-Branch ab `origin/main`, Tests, PR, kein Merge und kein Deployment ohne Freigabe.

## Stand

| Status | Anzahl |
|---|---:|
| Fertig | 73 |
| Teilweise | 103 |
| Offen | 1 |

**Aktueller Stand dieses Feature-Branches:** Nach Ergänzung von #164 ergibt sich 74 fertig / 102 teilweise / 1 offen. Dieser Branch-Zähler gilt erst nach erfolgreicher CI und Merge; main bleibt unverändert bei 73 / 103 / 1.

Aktueller Ausgangspunkt: main bei c44352852373de9b702664c6f35379d9b2c3d229 (PR #211, #212 und #213 gemergt). Der Support-Bereich #26–#40 ist damit auf main vollständig (15 fertig / 0 teilweise / 0 offen). #36 und #134 sind umgesetzt; /ticket zusammenfassung existiert bereits.

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

## Prüfstand 11.11.2026

- main = c44352852373de9b702664c6f35379d9b2c3d229 nach PR #213. Der zuletzt gespeicherte Repository-Auditvermerk nennt main als nicht geschützt und Issue #121 als offen; Branch-Schutz vor jedem Deployment erneut in GitHub prüfen.
- PR #211, #212 und #213 sind gemergt. Der Support-Bereich #26–#40 steht damit auf 15 / 0 / 0.
- PR #214: Locale-Grundlage für #10, beide erforderlichen CI-Workflows erfolgreich; Draft, #10 bleibt auf main offen.
- PR #215: AutoMod-Fehlalarm-Feedback für #25, beide erforderlichen CI-Workflows erfolgreich; Draft, zählt nicht zu main.
- PR #216: Ticket-Autocomplete-Teilumfang für #4, beide erforderlichen CI-Workflows erfolgreich; Draft, #4 bleibt teilweise.
- PR #217: Discord-Community-Vorschläge und Feedback für #45/#46; beim letzten Abruf war ein Workflow noch in Arbeit und der zweite in der Warteschlange. Nicht als fertig zählen, bis beide grün sind.
- docs/ROUTEN.md wurde in diesen Blöcken nicht aktualisiert; bei einer späteren Routenänderung Generator und Snapshot zusammen ausführen.

## Nächste Arbeit

1. **Locale-System (#10):** PR #214 ist eine getestete Grundlage, aber erst ein Teilumfang. Danach weitere Bot-Befehle, Modals/Buttons und Dashboard-Texte migrieren.
2. **Moderation (#11–#25):** PR #215 ergänzt den False-Positive-Rückkanal, ist noch Draft; gemeinsame Fallakten, Einsprüche, Beweismittel, Ausnahmen und Raid-Stufen bleiben offen/teilweise.
3. **Autocomplete (#4):** PR #216 deckt bestimmte Ticket- und Kategorieparameter ab; übrige passende Commands noch migrieren.
4. **Community (#45/#46):** PR #217 ergänzt Discord-Einreichung und Abstimmung; erst nach beiden erfolgreichen CI-Workflows bewerten.
5. Danach die übrigen teilweise bewerteten Punkte bereichsweise mit Verhaltenstests, Rechte- und Datenschutzprüfung.

## Hinweise für die Weiterarbeit

- Arbeitsverzeichnis ist ein eigener Git-Worktree; der Haupt-Checkout enthält nicht eingecheckte Nutzeränderungen und bleibt unberührt.
- `docs/ROUTEN.md`, `docs/routen_snapshot.json` und `docs/DATENLANDKARTE.md` werden mit `python tools/route_map.py` und
  `python tools/data_map.py` erzeugt. Sie waren auf `main` veraltet; der große Diff in Block 1 stammt daher.
- Lokale Tests unter Windows: Backup-/Restore-Tests scheitern an Dateisperren (WinError 32) und die Browser-E2E-Tests am
  lokalen Setup – auch auf unverändertem `main`. Maßgeblich ist die CI (Linux).
- `pulse_ultimate.py` und `webserver.py` stehen nicht in der Ruff-Liste (`ruff.toml`); neue Module dort eintragen.
