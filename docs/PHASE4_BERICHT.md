# Phasenbericht Phase 4 – Architektur & Wartbarkeit

Branch `verbesserung/phase-4-architektur` (von `main` = 7.12.0), **nicht gepusht, nicht gemergt, nicht deployt**. Stand 20.10.2026.

## 1. Erledigt

| ID | Commits | Testnachweis |
|---|---|---|
| A-03 Ein Rechte-Modell | 7e62315, 6c5f12f | `tests/test_permissions.py` (12) – Modell, Sicherheitsfix, E-4, Routen-Matrix für 111 GET- und 120 schreibende Routen |
| A-09 IDs zentral | 6dc0bcb | `tests/test_ids.py` – Werte-Snapshot; Review hat alle 30 Konstanten mit `main` verglichen: 0 Abweichungen |
| A-07 Code-Qualität | 50f65b9 | `tests/test_lint_findings.py` – Dashboard-Profil-Speichern repariert, keine undefinierten Namen im ganzen Code |
| A-08 Testbasis | b29f10e | `tests/fakes.py` + `tests/test_fakes.py` |
| A-05 Migrationen (Grundlage) | b29f10e, 6c5f12f | `tests/test_migrations.py` (5) – einmalig, Sicherung davor, Fehlschlag inkl. Schemaänderung vollständig zurückgerollt |
| A-04 Import ohne Seiteneffekte (Teil) | cbe3f45 | Frischinstallation getestet; `lifespan` statt `on_event` |
| A-01 Entscheidungsvorlage | 6d58788, 6586431 | `docs/SEITEN_ALT_NEU.md` |
| A-02 Schritt 1 | 6dc0bcb | Konstanten/IDs ausgelagert (= A-09) |
| P-06 Rest | 6586431 | Auto-Backup-Prüfung in der Job-Registry |

**Gates** (venv `Q:\venv`, Python 3.14, ohne Discord):
- `python -m compileall -q .` → OK
- `python tests/feature_smoke.py` → „Pulse feature smoke test: OK“
- `python tests/test_pulse_intelligence.py` → OK
- `python tests/smoke_test.py` → „Pulse smoke test: OK“
- `python -m ruff check .` → „All checks passed!“
- `python -m unittest discover -s tests` → **419 Tests OK** (nach Phase 3: 394)

**Review** durch einen Subagenten mit frischem Kontext: keine kritischen, 2 mittlere und 6 geringe Befunde.
- Behoben in 6c5f12f:
  - Migrationen rollten Schemaänderungen nicht zurück.
  - Die Routen-Matrix deckte schreibende Routen nicht ab.
  - Die Rechteschlüssel waren nicht gegeneinander abgesichert.
  - Pre-Migration-Sicherungen wurden nie aufgeräumt.
  - Der Docstring von `init_runtime` versprach zu viel.
- Die übrigen Befunde (Rechteänderung nennen, Lint-Umfang) stehen unten.

**Geänderte Test-Asserts:**
- **Feste Discord-IDs:** In `feature_smoke.py` (20) und `test_application_eligibility.py` (4) wurden sie im Quelltext der Module gesucht. Jetzt prüfen diese Stellen den Wert in `pulse_ids.py` und den Verweis im Modul. Der Snapshot-Test sichert zusätzlich alle Werte.
- **Profilregel:** Drei Asserts zur Profilregel zeigten auf den entfernten Laufzeit-Patch. Sie prüfen jetzt `pulse_permissions.py` plus einen Verhaltenstest.

## 2. Verhalten vorher → nachher

- **Sicherheitsfix bei den Rechten:**
  - Zusatzrechte (Ankündigungen, Übergaben, Beförderungsanträge, Teamstatus, Activity Check, Melonly-Kick/Ban/BOLO) kamen bisher aus den Einstellungen **jeder** Discord-Rolle eines Mitglieds. Das galt auch für Rollen, die keine Teamrolle (mehr) sind.
  - Jetzt geben **nur Teamrollen** und die ihnen zugeordneten Dashboard-Profile Rechte, wie bei den Grundrechten schon immer.
  - Admins und der Serverinhaber haben weiterhin alle Rechte. Die Vererbung über das Beförderungsrecht ist unverändert.
- **E-4 umgesetzt:** Eine Teamrolle, die in den Einstellungen nie konfiguriert wurde, gibt nur noch „Dashboard sehen“. Analytics inklusive Team-Exporte gibt es nur, wenn es ausdrücklich gesetzt ist.
- **Fehler behoben:** Beim Speichern einer digitalen Zwischenrolle kam bisher nach dem Speichern eine Fehlerseite, und der Audit-Eintrag fehlte. Jetzt gibt es eine normale Bestätigung mit Audit-Eintrag.
- **Feste IDs** (Kanäle, Rollen, Warnstufen, Büroräume, Ticketpanels) stehen jetzt an einer Stelle: `pulse_ids.py`, mit Kurzbeschreibung. Die Werte sind unverändert.
- **Start:** `webserver` legt beim Import keine Dateien mehr an, das passiert ausdrücklich beim Start. Das Dashboard nutzt die aktuelle FastAPI-Startlogik (`lifespan`).
- **Für die Zukunft:**
  - Datenbankänderungen laufen als versionierte Migration mit Sicherung vorher; bei Fehlern bleibt nichts halb geändert.
  - Die CI installiert jetzt die Abhängigkeiten (die Tests konnten dort vorher gar nicht laufen) und prüft mit `ruff`.

## 3. Verworfen oder verschoben

- **A-01 Zusammenführen/Löschen** wartet auf deine Entscheidung zu `docs/SEITEN_ALT_NEU.md` (E-6). Vorbereitet sind 22 tote Handler bzw. Schleifen und 19 Aufgaben mit mehreren Adressen.
- **A-02** ist bisher nur in Schritt 1 umgesetzt (IDs). Offen sind Auth/Session, Persistenz-Helfer, UI-Bausteine und das Aufteilen von `POST /action`. Das geht nur in kleinen, einzeln getesteten Schritten; ein großer Umbau wäre gegen die Planregeln.
- **A-04:** `pulse_pro`, `pulse_features` und `pulse_next` legen beim Registrieren noch Tabellen bzw. `backups/` an. Das wird mit A-02 verschoben.
- **A-05:** Die Migrationsliste ist noch leer. Die JSON-→-SQLite-Umzüge (`shifts`, `logs`, `applications`) folgen einzeln, jeweils mit Lese-Fallback.
- **A-06 (Jinja2):** verschoben bis zur ersten neuen Seite. In dieser Phase entstand keine.
- **P-10 Rest** (rund 320 `print`) bleibt offen und wird mit A-02 modulweise umgestellt.
- **Korrektur zu A-07:** Der zweite gefundene Fehler (Ticket-Erinnerung) lag in einer Housekeeping-Schleife in `pulse_features`, die gar nicht gestartet wird. Er hatte im Betrieb keine Auswirkung, die Schleife steht jetzt auf der Liste toten Codes.
- **Lint-Umfang:** Der vollständige Regelsatz gilt bisher nur für die neuen Module. Der Bestand hat 26 ungenutzte Importe und 26 ungenutzte Variablen. Undefinierte Namen prüft ein Test schon im ganzen Code.

## 4. Risiken und Schritte außerhalb des Codes

- **Rechte bitte prüfen:** Nach dem Deploy unter *Einstellungen & Rechte* nachsehen.
  - Hat jemand Rechte über eine Rolle bekommen, die **keine** Teamrolle ist? Diese Rechte wirken nicht mehr. Wer sie weiter braucht, bekommt sie über eine Teamrolle oder ein Dashboard-Profil.
  - Nie konfigurierte Teamrollen haben keinen Analytics-Zugriff mehr. Bei Bedarf dort „Analytics ansehen“ setzen.
- **CI:** Der neue Installationsschritt braucht Linux-Pakete für Python 3.14. Ob `davey`/`PyNaCl` dort sauber installieren, zeigt der erste CI-Lauf nach dem Push.

## 5. Offene Fragen an dich

1. **Doppelte Seiten (E-6):** Bitte `docs/SEITEN_ALT_NEU.md` durchsehen.
   - Teil A (toter Code löschen): ja oder nein?
   - Teil B: je Zeile führend, beide behalten oder anders.
2. **Freigabe:** Darf ich Phase 4 nach `main` mergen und pushen? Vorschlag: Version 7.13.0 mit CHANGELOG-Eintrag, inklusive Hinweis auf die Rechteänderung.
