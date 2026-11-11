# Pulse 177 – Umsetzungsfortschritt

Arbeitsstand der Mission „177 von 177“. Einzelstatus je Funktion: `docs/PULSE_FEATURE_AUDIT_177.md`.
Regeln: kleine Blöcke, je Block ein Feature-Branch ab `origin/main`, Tests, PR, kein Merge und kein Deployment ohne Freigabe.

## Stand

| Status | Anzahl |
|---|---:|
| Fertig | 69 |
| Teilweise | 107 |
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

## Nächste Arbeit

1. **Support (#26–#28):** automatische Ticketzuteilung, Kategorie-SLA und Überfälligkeits-Eskalation an einem durchgängigen Ablauf vervollständigen.
2. **Locale-System (#10):** Spracheinstellung und Übersetzungsinfrastruktur; anschließend Kernbefehle und weitere Bot-Antworten systematisch migrieren.
3. **Moderation (#11–#25):** gemeinsame Fallakten, Einsprüche, Beweismittel, Ausnahmen, Raid-Stufen und False-Positive-Rückkanal.
4. Danach die übrigen teilweise bewerteten Punkte bereichsweise, jeweils mit Verhaltenstests und Rechte-/Datenschutzprüfung.
## Hinweise für die Weiterarbeit

- Arbeitsverzeichnis ist ein eigener Git-Worktree; der Haupt-Checkout enthält nicht eingecheckte Nutzeränderungen und bleibt unberührt.
- `docs/ROUTEN.md`, `docs/routen_snapshot.json` und `docs/DATENLANDKARTE.md` werden mit `python tools/route_map.py` und
  `python tools/data_map.py` erzeugt. Sie waren auf `main` veraltet; der große Diff in Block 1 stammt daher.
- Lokale Tests unter Windows: Backup-/Restore-Tests scheitern an Dateisperren (WinError 32) und die Browser-E2E-Tests am
  lokalen Setup – auch auf unverändertem `main`. Maßgeblich ist die CI (Linux).
- `pulse_ultimate.py` und `webserver.py` stehen nicht in der Ruff-Liste (`ruff.toml`); neue Module dort eintragen.
