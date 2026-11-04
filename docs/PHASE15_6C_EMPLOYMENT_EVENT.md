# Phase 15.6c – Minimiertes Einstellungsereignis

**Status:** Implementierung in eigenem PR · keine Produktionsmigration  
**Basis:** `main` @ `742ec1d59e248c895761cb01f366dab8b3b964bc`  
**Ziel:** Ein bestätigter Team-Eintritt bleibt unabhängig von vollständigen Bewerbungsdaten nachvollziehbar.

## 1. Datenmodell

Das aktuelle Beschäftigungsereignis liegt als `employment_event` im bestehenden Datensatz des Teammitglieds in `team_data.json`.

| Feld | Zweck |
|---|---|
| `version` | Version des kleinen Ereignisformats (aktuell 1) |
| `user_id` | Discord-ID der eingestellten Person |
| `hired_at` | Zeitzonenbehafteter ISO-8601-Einstellungszeitpunkt |
| `actor_id` | Discord-ID der handelnden Person |
| `source` | Bestätigter Herkunftspfad: `application_interview`, `manual_hire` oder `team_role_assignment` |
| `audit_ref` | ID des zugehörigen Eintrags in `audit_logs.json` |
| `career_path` | Optionaler, begrenzter Laufbahnschlüssel (kein Freitext) |

Nicht gespeichert werden Bewerbungsantworten, Test-/Praxisantworten, Interviewnotizen, Bewertungen, Entscheidungsgründe, Namen oder Anhangsinhalte. Eine Bewerbungs-ID wird im Einstellungsereignis nicht dupliziert; der zugehörige Audit-Eintrag enthält den fachlichen Kontext.

Das Feld enthält jeweils den Nachweis der aktuellen Teamzugehörigkeit. Bei einer späteren, separat bestätigten Wiedereinstellung wird es ersetzt – analog zum bisherigen Verhalten von `team_since`. Frühere Aktionen bleiben über das Audit nachvollziehbar.

## 2. Bestätigte Schreibpfade

Ein Ereignis wird erst nach erfolgreicher Discord-Rollenänderung geschrieben:

1. `handle_action` / `hire_after_interview`: das verpflichtende Bewerbungsgespräch ist dokumentiert und Discord hat die Teamrolle vergeben sowie die Bewerberolle entfernt.
2. `handle_action` / `hire`: die manuelle Einstellung hat die Teamrolle erfolgreich vergeben.
3. `team_role_manager_save`: eine Person wechselt von keiner konfigurierten Teamrolle zu mindestens einer Teamrolle.

Das bloße Setzen von `accepted` über die Suite-Statusverwaltung erzeugt ausdrücklich **kein** Einstellungsereignis. Eine Einladung zum Gespräch ist ebenfalls keine Einstellung.

## 3. Auflösung von „Team seit“

Die Laufzeitauflösung behält die bestehende Kompatibilität und verwendet folgende Reihenfolge:

1. Gültiges vorhandenes `team_since`.
2. Gültiges `employment_event.hired_at`, wenn die enthaltene `user_id` zur Person passt.
3. Legacy-Fallback: ältestes gültiges `decided_at` einer Bewerbung derselben Person mit Status exakt `hired` oder `accepted`.
4. Discord-`joined_at`, sonst `unbekannt`.

Damit bleiben bisherige Teamakten unverändert priorisiert. Neue bestätigte Eintritte können zukünftig ohne vollständige Bewerbungsantworten aufgelöst werden.

## 4. Sicherheit und Grenzen

- Keine automatische Bestandsbefüllung und kein Backfill alter `accepted`-/`hired`-Fälle.
- Keine Änderung an Retention, Fristen, Datenschutzexport, Bewerbungsstatus oder Bewerbungsformularen.
- Keine Löschung, Anonymisierung, JSON-/SQLite-Umschaltung oder Produktivmigration.
- Keine Änderung an Live-Daten oder am VPS.
- Ereigniserstellung ist für denselben Audit-Verweis idempotent; eine neue bestätigte Wiedereinstellung ersetzt den aktuellen Ereignisdatensatz.

## 5. Tests

`tests/test_employment_events.py` prüft das minimierte Schema, optionale Laufbahndaten, Eingabevalidierung, Zeitzonen, Idempotenz, Wiedereinstellung und die Zuordnung zur richtigen Discord-ID. AST-basierte Regressionstests sichern alle drei bestätigten Schreibpfade, die Fallback-Reihenfolge von `team_since_for`, die Rückgabe der Audit-ID und die Abgrenzung zur Suite-Statusänderung ab.

Die normalen CI-Workflows bleiben zusätzlich verpflichtend. Ein grüner CI-Lauf ist kein Merge- oder Deployment-Auftrag.
