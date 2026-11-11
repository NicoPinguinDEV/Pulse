# Pulse 177 – Umsetzungsfortschritt

Arbeitsstand der Mission „177 von 177“. Einzelstatus je Funktion: `docs/PULSE_FEATURE_AUDIT_177.md`.
Regeln: kleine Blöcke, je Block ein Feature-Branch ab `origin/main`, Tests, PR, kein Merge und kein Deployment ohne Freigabe.

## Stand

| Status | Anzahl |
|---|---:|
| Fertig | 58 |
| Teilweise | 110 |
| Offen | 9 |

Ausgangspunkt: `main` bei `be60982` (54 / 109 / 14 nach den PRs #190–#198, #200, #201).
Korrektur beim Abgleich: #36 und #134 sind nicht offen, sondern teilweise umgesetzt – `/ticket zusammenfassung` existiert bereits.

## Block 1 – Workflow-System (#144, #145, #148, #149)

- Branch: `feature/workflow-versioning-retries-approvals`
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

## Reihenfolge der nächsten Blöcke

Abhängigkeiten: #69 baut auf #61 auf; #134 liefert die Zusammenfassungslogik für #36 und #135; #82 braucht #80.

1. **Block 2 – Zusammenfassungen (#36, #134, #135):** `pulse_ticket_extras.build_ticket_summary` um Sachstand, offene Fragen,
   erledigte Schritte und nächsten Schritt erweitern, im Ticketdetail des Dashboards anzeigen, Bewerbungsantworten
   strukturiert aufbereiten. Keine Bewertung, keine externe KI. Dabei den Fehler mit doppelt maskierten Zeilenumbrüchen
   (`\\n`) in `cogs/ticket_tools.py` (Zeilen 195–377) mit Regressionstest beheben.
2. **Block 3 – Persönliche Ansichten (#61, #69):** gespeicherte Filter je Nutzer nach dem Muster der Favoriten (`pulse_home.py`),
   danach Layouts/Widget-Reihenfolge.
3. **Block 4 – AutoMod-Testmodus (#17)** mit Fehlalarm-Rückkanal (#25).
4. **Block 5 – Kompetenzmatrix und Arbeitslast (#80, #82)**, danach #26/#40/#81.
5. **Block 6 – Schichttausch (#84)** mit Dienstabdeckung (#85).
6. **Block 7 – Personalbedarfsprognose (#128)**, danach #127.
7. **Block 8 – Locale-System (#10).**
8. Danach die 108 teilweise fertigen Punkte bereichsweise in der Reihenfolge des Auftrags.

## Hinweise für die Weiterarbeit

- Arbeitsverzeichnis ist ein eigener Git-Worktree; der Haupt-Checkout enthält nicht eingecheckte Nutzeränderungen und bleibt unberührt.
- `docs/ROUTEN.md`, `docs/routen_snapshot.json` und `docs/DATENLANDKARTE.md` werden mit `python tools/route_map.py` und
  `python tools/data_map.py` erzeugt. Sie waren auf `main` veraltet; der große Diff in Block 1 stammt daher.
- `pulse_ultimate.py` und `webserver.py` stehen nicht in der Ruff-Liste (`ruff.toml`); neue Module dort eintragen.
