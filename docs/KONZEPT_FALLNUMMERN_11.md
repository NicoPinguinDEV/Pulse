# Entscheidungsvorlage: Gemeinsamer Fallnummernkreis (#11)

**Stand:** 13.11.2026 · **Status:** Konzept, nicht umgesetzt · **Bezug:** Audit #11, `pulse_case_registry.py` (PR #237)
**Grenzen dieser Phase:** keine Migration, keine neue Tabelle, keine Änderung an Fallnummern oder Datenquellen, kein Deployment.

---

## 1. Kurzfassung und Empfehlung

| Frage | Antwort |
|---|---|
| Brauchen wir einen gemeinsamen Nummernkreis? | **Ja, aber schmal.** Für das Nachschlagen genügen die Referenzen aus PR #237. Es fehlt aber eine Nummer, die beim Anlegen feststeht, in DMs/Einsprüchen/Logs zitiert wird und sich nie ändert. Ohne zentrale Vergabe lässt sich außerdem nicht nachweisen, dass kein Fall fehlt (Lückenlosigkeit). |
| Empfohlene Variante | **Variante B – zentrales Fallregister** (`case_registry`, additive Tabelle in `pulse.db`) mit fortlaufender Nummer `PF-JJJJ-NNNNN`. Die vier Quellen bleiben unverändert; das Register ordnet jeder neuen Nummer genau eine bestehende Quell-ID zu. |
| Was vorher repariert werden muss | **Phase 0** (unabhängig von der Entscheidung, siehe §3): KI-bestätigte Bürgermaßnahmen erzeugen keinen `mod_cases`-Fall, und `case_number()` vergibt TF-Nummern ohne Sperre. |
| Aufwand | Phase 0: klein bis mittel · Phase 1–3: mittel, verteilt auf 3 PRs |
| Risiko | Gering, solange die Vergabe additiv bleibt und die Quell-IDs unverändert führend bleiben. |

**Zur Freigabe:** Phase 0 sofort, Variante B ab Phase 1. Die Entscheidungspunkte stehen in §12.

---

## 2. Ausgangslage (aus dem Code erhoben)

| Quelle | Ablage | ID heute | Vergabe | Anlegestellen | Backup | Aufbewahrung |
|---|---|---|---|---|---|---|
| **BM** Bürger-Moderation | `pulse.db` `mod_cases` | `MOD-` + 6 Hex (Zufall) | `secrets.token_hex(3)`, PK-Konflikt theoretisch möglich (1 : 16,7 Mio.) | `pulse_mod_cases.create_case` ← `cogs/mod_cases.py` | JSON-Export (`TABLES`) und ZIP | unbegrenzt |
| **TF** Team-/Spielerfall | `pulse.db` `ultimate_cases` (+ `ultimate_case_events`) | `PX-JJJJ-NNNN` (fortlaufend) | `pulse_ultimate.case_number()`: `MAX()+1` **außerhalb einer Transaktion**, `UNIQUE` fängt Doppelte erst beim INSERT ab | `pulse_ultimate.py:1304`, `:2963` (Automation), `pulse_next.py:752` (Workflow) | nur ZIP | unbegrenzt |
| **TW** Teamwarnung | `team_data.json` `warns_list` | `warn_` + 6 Hex (Zufall) | `uuid4().hex[:6]`; Altdaten ohne ID werden beim Lesen nachgezogen | `webserver.issue_team_warning` ← Dashboard, `/warn`, `cogs/pulse_suite.py`, KI-Prüfung | nur ZIP | aktiv/abgelaufen/widerrufen bleiben erhalten |
| **KI** KI-Moderation | `ai_moderation_cases.json` | 10 Hex (Zufall) | `uuid4().hex[:10]` | `cogs/ai_moderation.py:539`, `:1006` | nur ZIP (`pulse_backup.JSON_FILES`) | **30 Tage**, außer offen |

Zusätzlich vorhanden:
- `pulse_case_registry.py` (PR #237): lesende typisierte Referenzen `BM:/TF:/TW:/KI:` und der Gesamtverlauf (#12).
- `pulse_migrations.py`: versionierte Migrationen mit Sicherung vorab, je Migration eine Transaktion. Die Liste `MIGRATIONS` ist **noch leer**, der Mechanismus wurde also produktiv noch nie benutzt.
- Audit: `webserver.log_audit` schreibt in die JSON-Datei `audit_log` (begrenzt auf 5000 Einträge); `pulse_db.record_event` schreibt in die Tabelle `events`.
- SQLite läuft mit WAL und `busy_timeout=3000`. `BEGIN IMMEDIATE` wird an zwei Stellen in `pulse_db.py` bereits genutzt.

---

## 3. Befunde, die unabhängig von #11 zu beheben sind (Phase 0)

1. **KI-bestätigte Bürgermaßnahmen erzeugen keine Fallakte.** `cogs/ai_moderation.py::_apply_citizen_action` führt Verwarnung, Timeout, Kick oder Ban direkt in Discord aus, ohne `pulse_mod_cases.create_case`.
   - Folgen: kein `MOD-`-Fall, kein Einspruchsbutton (#15), keine Statistik (#24).
   - Nach 30 Tagen löscht die Aufbewahrungsregel den KI-Fall. Ein Ban ist dann **nirgends mehr belegt**.
   - Schwere: hoch (Nachvollziehbarkeit und Einspruchsrecht).
2. ~~`ai_moderation_cases.json` ist in keinem Backup.~~ **Widerlegt (Nachprüfung 13.11.2026):** Die Datei steht in `pulse_backup.JSON_FILES` und wird über `PULSE_AI_MODERATION_CASES_FILE` am tatsächlichen Ablageort ins Voll-ZIP gesichert (`pulse_backup.py:44`, `:78`). Nur der JSON-Export von `pulse_db.TABLES` enthält sie nicht, weil sie keine Tabelle ist. Kein Handlungsbedarf.
3. **`case_number()` ist nicht nebenläufigkeitssicher.**
   - Zwei gleichzeitige Anlagen, etwa Dashboard und Automation im selben Moment, lesen denselben `MAX()`-Wert.
   - Die zweite Anlage scheitert an `UNIQUE` mit einem 500er bzw. einem fehlgeschlagenen Automationsschritt.
   - Schwere: mittel. Es entstehen keine Datenfehler, aber die Anlage geht verloren.

Die Punkte 1 und 3 müssen vor jeder zentralen Vergabe stehen. Sonst gäbe es Fälle, die keine Nummer bekommen können (1), und eine Vorlage für unsichere Vergabe (3).

---

## 4. Varianten

| | A – Nur Registry (Ist-Stand #237) | **B – Zentrales Fallregister (empfohlen)** | C – Eine Fallnummer in allen Quellen |
|---|---|---|---|
| Idee | Typisierte Referenz `BM:MOD-…` wird beim Lesen berechnet | Neue Nummer `PF-…` wird beim Anlegen in **einer** Registertabelle vergeben und auf die Quell-ID abgebildet | Jede Quelle erhält eine Spalte bzw. ein Feld `pulse_case_no`, oder die IDs werden ersetzt |
| Eindeutige Referenz | ✅ | ✅ | ✅ |
| Zitierbare, stabile Nummer ab Anlage | ⚠️ quellabhängig | ✅ | ✅ |
| Lückenlos und chronologisch über alle Arten | ❌ | ✅ | ✅ |
| Änderungen an Quellen | keine | keine (nur Aufruf beim Anlegen) | Schema von 2 Tabellen und 2 JSON-Formaten |
| Migration | keine | additive Tabelle, Altfälle einmalig registriert | Umschreiben bestehender Daten |
| Rollback | – | Tabelle ignorieren, Quellen funktionieren unverändert | aufwendig |
| Risiko | keins | gering | hoch |

**Zur Notwendigkeit:** Variante A genügt dem Lesen und Verlinken, aber nicht diesen Anforderungen:
- **Eine Nummer für die betroffene Person:** In DM, Einspruch und Discord-Audit-Log soll dieselbe Nummer stehen, egal welche Akte den Fall führt.
- **Prüfbarkeit:** Die Leitung kann nur bei fortlaufender Vergabe sehen, ob ein Fall fehlt, etwa durch ein Restore oder einen gelöschten KI-Fall.
- **Zukünftige Fallarten:** Spielermeldungen #49 oder Beschwerden würden ohne zentrale Vergabe einen fünften Nummernkreis bekommen.

Wenn diese drei Punkte für BOCHUM RP **nicht** wichtig sind, reicht A dauerhaft. #11 würde dann mit der Begründung „Referenzen statt Nummernkreis“ als bewusst abweichend abgeschlossen (Entscheidung E1).

---

## 5. Zielarchitektur (Variante B)

```
 Anlage eines Falls (BM / TF / TW / KI→BM|TW)
        │
        ▼
 pulse_case_numbers.allocate(kind, source_id, guild_id, person_id)   ← einzige Vergabestelle
        │  BEGIN IMMEDIATE · Zähler +1 · INSERT case_registry · COMMIT
        ▼
 Quelle schreibt wie heute (MOD-…, PX-…, warn_…)  – Quell-ID bleibt führend
        │
        ▼
 pulse_case_registry (PR #237): resolve("PF-2026-00042") → Register → Quelle → Rechteprüfung
```

- **Führend bleibt die Quelle.** Das Register enthält nur Zuordnungen und keine Fachdaten: keinen Grund, keinen Inhalt, keine Sanktion. Damit entsteht keine zweite Datenhaltung.
- **Eine Vergabestelle:** das neue Modul `pulse_case_numbers.py`. Alle Anlegestellen aus §2 rufen nur `allocate()` auf.
- **Lesen:** `pulse_case_registry.parse_ref` erkennt zusätzlich `PF-JJJJ-NNNNN` und `PF:…` und löst die Nummer über das Register zur Quell-ID auf. Danach gilt dieselbe Rechteprüfung wie heute. `BM:/TF:/TW:/KI:` und die nackten Alt-IDs bleiben gültig.

---

## 6. Nummernformat

**`PF-JJJJ-NNNNN`**, z. B. `PF-2026-00042`.

- `PF` steht für „Pulse-Fall“. Das Präfix kollidiert mit keinem bestehenden Präfix (`MOD-`, `PX-`, `warn_`, KI-Hex).
- `JJJJ` ist das Kalenderjahr der Vergabe in Europe/Berlin. Der Zähler beginnt jedes Jahr bei 1.
- `NNNNN` ist fünfstellig mit führenden Nullen und läuft bei Bedarf ohne Formatbruch sechsstellig weiter. Das Parsing akzeptiert 1–7 Ziffern.
- Die Nummer verrät die Fallart **nicht**. Sie wird auch Betroffenen genannt, und aus „PF-…“ soll nicht ablesbar sein, ob es eine Teamwarnung oder ein Bürgerfall ist.
- Optionale Prüfziffer (Luhn mod 10, `PF-2026-00042-7`) gegen Tippfehler bei mündlicher Weitergabe. Das ist Entscheidung E3; Standard ist ohne.

---

## 7. Datenmodell (nur Entwurf, nicht anlegen)

```sql
-- additiv in pulse.db; keine bestehende Tabelle wird verändert
CREATE TABLE case_registry (
    case_no      TEXT PRIMARY KEY,                -- 'PF-2026-00042'
    year         INTEGER NOT NULL,
    seq          INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('BM','TF','TW','KI')),
    source_id    TEXT NOT NULL,                   -- 'MOD-1A2B3C' | 'PX-2026-0007' | 'warn_ab12cd' | KI-Hex
    guild_id     TEXT NOT NULL DEFAULT '',
    person_id    TEXT NOT NULL DEFAULT '',        -- nur Discord-ID, kein Name
    state        TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','active','void')),
    origin       TEXT NOT NULL DEFAULT 'new' CHECK (origin IN ('new','backfill')),
    created_at   TEXT NOT NULL,
    void_reason  TEXT NOT NULL DEFAULT '',
    UNIQUE (year, seq),
    UNIQUE (kind, source_id)                      -- eine Quell-ID bekommt nie zwei Nummern
);
CREATE TABLE case_registry_counter (
    year INTEGER PRIMARY KEY,
    last_seq INTEGER NOT NULL
);
CREATE INDEX idx_case_registry_person ON case_registry(person_id, created_at);
```

- **Keine Fremdschlüssel zu den Quellen.** Zwei der Quellen sind JSON-Dateien, und ein Restore einer einzelnen Quelle darf das Register nicht kaskadierend löschen.
- **`state`:**
  - `reserved`: Nummer vergeben, Quelle noch nicht bestätigt.
  - `active`: Quelle hat den Fall geschrieben.
  - `void`: Anlage gescheitert, die Nummer bleibt sichtbar verbraucht. Eine Lücke ist damit erklärt, nicht verschwiegen.
- **Datenschutz:** nur IDs und Zeitstempel, keine Inhalte.

---

## 8. Vergabeprozess

```text
allocate(kind, source_id, guild_id, person_id):
  BEGIN IMMEDIATE                                   -- Schreibsperre auf pulse.db, busy_timeout 3 s
    if row := SELECT … WHERE kind=? AND source_id=?:
        COMMIT; return row.case_no                  -- idempotent: Wiederholung liefert dieselbe Nummer
    year = berlin_today().year
    UPSERT case_registry_counter(year) SET last_seq = last_seq + 1 RETURNING last_seq
    INSERT case_registry(..., state='reserved')
  COMMIT
  return case_no
confirm(case_no)  -> state='active'                 -- nach erfolgreichem Schreiben der Quelle
void(case_no, reason) -> state='void'               -- Anlage endgültig gescheitert
```

**Reihenfolge je Quelle**, damit der Fall auch bei einem Absturz zwischen den Schritten nicht verloren geht:

| Quelle | Ablauf |
|---|---|
| BM | Quell-ID erzeugen → `allocate` → `INSERT mod_cases` → `confirm`. Alles liegt in `pulse.db`; die Anlage kann deshalb in **derselben** Transaktion wie `allocate` laufen, und `reserved` tritt bei BM nicht auf. |
| TF | Wie BM, ebenfalls in `pulse.db` und damit in einer Transaktion. `case_number()` wird dabei auf denselben `BEGIN IMMEDIATE`-Zähler umgestellt; das behebt Befund 3. |
| TW | Quell-ID erzeugen → `allocate` (reserved) → `team_data.json` speichern → `confirm`. Ein Absturz dazwischen hinterlässt `reserved`. Ein Abgleich im **bestehenden** 5-Minuten-Housekeeping setzt `reserved` älter als 10 Minuten auf `active`, wenn die Warnung existiert, sonst auf `void`. Es gibt keinen neuen Hintergrundjob. |
| KI | Erst wenn ein Mensch eine Maßnahme bestätigt, wird ein BM- oder TW-Fall angelegt (Phase 0) und **dieser** bekommt die Nummer. Reine Verdachtsfälle ohne Maßnahme bekommen keine Nummer, weil sie nach 30 Tagen verfallen und sonst Nummern ins Leere zeigen würden (Entscheidung E2). |

- **Discord-Fehler nach der Anlage:** Scheitert die Discord-Aktion danach mit 403 oder 404, markiert BM den Fall schon heute als `failed`. Die Nummer bleibt `active` und belegt den Versuch.
- **Fehlerverhalten:** Scheitert `allocate` (DB gesperrt, Timeout), wird **die Anlage abgebrochen** und die Person sieht eine klare Meldung. Ein stiller Fall ohne Nummer entsteht nicht, und es wird nicht endlos wiederholt. Ausnahme TW: Weil das Fallrisiko dort im JSON liegt, entscheidet E4, ob TW bei Registerausfall ohne Nummer weiterlaufen darf (fail-open, anschließend nachgetragen) oder abbricht (fail-closed).

---

## 9. Migration und Rückwärtskompatibilität

**Migration** (erst nach Freigabe, über `pulse_migrations.MIGRATIONS`; das wäre die erste produktive Migration):
1. `case_registry_v1`: legt beide Tabellen an. Rein additiv; ein Rollback lässt die Tabellen ungenutzt liegen.
2. **Backfill als eigener, auditierter Admin-Vorgang**, kein Automatismus beim Start:
   - Er registriert alle bestehenden BM-, TF- und TW-Fälle mit `origin='backfill'`, sortiert nach Anlagezeit. Die Nummern erhalten die Jahre der jeweiligen Anlage.
   - Bestehende Quell-IDs werden **nicht** verändert.
   - Idempotent dank `UNIQUE(kind, source_id)`.
   - Mit Trockenlauf und Bericht: Anzahl je Quelle, Fälle ohne lesbare Zeit, Dubletten. Alt-Warnungen ohne ID werden nur gemeldet und nicht geschrieben.
   - Entscheidung E5: Backfill ja oder nein. Ohne Backfill haben nur neue Fälle eine `PF`-Nummer.

**Kompatibilität:**

| Bestand | Verhalten nach Umsetzung |
|---|---|
| `MOD-…`, `PX-…`, `warn_…`, KI-IDs | unverändert gültig in Commands, Routen, DMs und Einsprüchen |
| `BM:/TF:/TW:/KI:`-Referenzen (#237) | unverändert gültig |
| `/mod akte`, `/mod einspruch-entscheiden fall_id`, Einspruchs-`custom_id` | unverändert. Die `custom_id` enthält weiter die `MOD-`-ID, und bereits zugestellte DM-Buttons funktionieren weiter |
| `/mod fall`, `/mod-cases/verlauf?ref=` | akzeptieren zusätzlich `PF-…` |
| DM-Text, Bestätigungen, Audit-Log | zeigen zusätzlich `PF-…`, die Quell-ID bleibt daneben stehen |
| JSON-Export, CSV | neue Spalte nur in neuen Exporten, bestehende Spaltenreihenfolge unverändert |

---

## 10. Backup, Restore, Integrität

- **Backup:**
  - `case_registry` und `case_registry_counter` kommen in `pulse_db.TABLES`, also in den JSON-Export und in den ZIP-Backup über die ganze `pulse.db`.
  - `ai_moderation_cases.json` ist bereits Teil der ZIP-Sicherung.
- **Restore-Risiko:** `pulse.db` und `team_data.json` können auf unterschiedliche Zeitpunkte zurückgesetzt werden. Danach gibt es entweder Nummern ohne Warnung oder Warnungen ohne Nummer.
  - Ein lesender **Integritätsbericht** im Admin-Center zeigt beides an.
  - Reparaturen sind nur ausdrücklich bestätigte Aktionen: fehlende nachtragen, verwaiste Nummern auf `void` setzen.
  - Nummern werden nie wiederverwendet. Der Zähler wird beim Restore auf `max(seq)` im Register angehoben, falls er darunter liegt.
- **Rollback:**
  - Code-Rollback ist jederzeit möglich, weil die Quellen ohne Register funktionieren.
  - Ein Schema-Rollback ist nicht nötig. Die Tabellen bleiben liegen, und die Sicherung vor der Migration liegt in `backups/pre_migration_case_registry_v1_*.db`.
- **Invarianten** (im Integritätsbericht und in Tests):
  - `(kind, source_id)` ist eindeutig.
  - `(year, seq)` ist eindeutig.
  - `last_seq` ≥ `max(seq)`.
  - Jede `active`-Nummer zeigt auf eine existierende Quell-ID.
  - Kein `reserved` ist älter als 10 Minuten.

---

## 11. Sicherheit und Datenschutz

- **Vergabe:** nur serverseitig in den bestehenden Anlegepfaden. Die Rechteprüfung der jeweiligen Anlage (`may_moderate`, Warn-Rang, Leitung) bleibt **vor** `allocate`. Es gibt keine eigene Route und keinen eigenen Command zum Vergeben.
- **Auflösen:** `PF-…` → Register → Quelle → `allowed_kinds()` aus #237. Wer die Fallart nicht sehen darf, bekommt dieselbe Antwort wie bei einer unbekannten Nummer. Fortlaufende Nummern laden zum Durchprobieren ein, gefunden wird dabei trotzdem nichts.
- **Kein Autocomplete** für `PF`-Nummern.
- **Betroffene:** Die Nummer erscheint in deren DM. Die Person erhält darüber keinen Zugriff auf fremde Fälle; die Selbstauskunft bleibt in `/datenschutz-anfrage`.
- **Datenschutzexport (#171):** Die Registerzeilen kommen über `person_id` in den Personenexport. Bei einer Löschanfrage gilt die Löschung in der Quelle. Die Registerzeile wird auf `void` gesetzt und `person_id` geleert; die Nummer bleibt als Lücke erklärt.
- **Audit:** Vergabe, Bestätigung, Verwerfen, Backfill und Reparatur schreiben `record_event` (Tabelle `events`). Manuelle Eingriffe zusätzlich `log_audit`.

---

## 12. Entscheidungspunkte zur Freigabe

| Nr. | Frage | Empfehlung |
|---|---|---|
| **E0** | Phase 0 (Befunde 1 und 3 aus §3) umsetzen? | **Ja, sofort.** Unabhängig von #11. |
| **E1** | Variante A (Referenzen genügen, #11 bewusst abweichend schließen) oder B (zentrales Register)? | **B** |
| **E2** | Bekommen KI-Verdachtsfälle ohne Maßnahme eine Nummer? | **Nein**, nur der daraus entstehende BM- oder TW-Fall |
| **E3** | Prüfziffer an der Nummer? | Nein (optional später) |
| **E4** | Teamwarnung bei Registerausfall: abbrechen (fail-closed) oder ohne Nummer anlegen und nachtragen (fail-open)? | **fail-closed**, wie BM und TF |
| **E5** | Bestehende Fälle per Backfill nummerieren? | **Ja**, als eigener, auditierter Admin-Vorgang mit Trockenlauf |
| **E6** | Die erste produktive Migration über `pulse_migrations` ausrollen? | Ja, mit Probelauf auf einer Kopie der aktuellen Produktions-DB vor dem Merge |

---

## 13. Testplan

| Bereich | Tests |
|---|---|
| Parallelität | 50 Threads bzw. Prozesse vergeben gleichzeitig: keine Doppelten, keine Lücken; `busy_timeout` überschritten führt zu einem sauberen Fehler ohne halbe Zeile |
| Idempotenz | `allocate` zweimal mit gleicher Quell-ID ergibt dieselbe Nummer; Backfill zweimal ergibt keine Änderung |
| Jahreswechsel | 31.12. 23:59:59 und 01.01. 00:00:00 Europe/Berlin, je ein neuer Zähler; Sommerzeit hat keinen Einfluss |
| Kollisionen | `PF-` gegen alle Alt-Präfixe; Parsing von 1–7 Ziffern; `PF:`-Präfix; Groß- und Kleinschreibung |
| Alte Referenzen | `MOD-`, `PX-`, `warn_`, `KI:`, `BM:` usw. lösen nach der Einführung identisch auf; bestehende Einspruchsbuttons (`custom_id`) funktionieren |
| Fehlerfälle | DB gesperrt; Quelle schreibt nicht (`void`); Absturz zwischen `allocate` und JSON-Speicherung (Abgleich setzt `active` bzw. `void`); defekte `team_data.json` |
| Restore | Register neuer als `team_data.json` und umgekehrt: Integritätsbericht korrekt; Zähler wird angehoben; keine Wiederverwendung |
| Rechte | `PF-`Nummer einer nicht erlaubten Fallart gibt dieselbe Antwort wie eine unbekannte; Durchprobieren fortlaufender Nummern findet nichts; Backfill und Reparatur nur für Admins |
| Datenschutz | Personenexport enthält Registerzeilen; Löschung setzt `void` und leert `person_id` |
| Migration | Sicherung vor Migration vorhanden; Fehler in der Migration hinterlässt kein halbes Schema; zweiter Start ändert nichts |
| Regression | `/mod`-Befehle, Einspruch, Statistik #24, Gesamtverlauf #12, Routen-Snapshot, Rechte-Sweep |

---

## 14. Risiken und Alternativen

| Risiko | Gegenmaßnahme |
|---|---|
| Erste produktive Migration überhaupt | Probelauf auf einer Kopie der Produktions-DB, Sicherung vorab, rein additiv |
| Zwei Ablagen (SQLite und JSON) für TW sind nicht atomar | `reserved`/`void` mit Abgleich im bestehenden Housekeeping; Integritätsbericht |
| Lücken durch `void` wirken wie fehlende Fälle | Lücken sind im Register mit Grund sichtbar |
| Mehr Schreiblast auf `pulse.db` | Eine Zeile je Fall; vernachlässigbar bei dem Fallaufkommen von BOCHUM RP |
| Personen zitieren künftig nur `PF-`Nummern | Alle Eingaben akzeptieren beide Formen |

**Verworfene Alternativen:**
- **C – Quell-IDs ersetzen oder Spalte in jeder Quelle:** bricht DMs, `custom_id`s und Exporte und verlangt eine Datenmigration in zwei JSON-Formaten.
- **UUIDs statt fortlaufender Nummern:** schützen besser vor Durchprobieren, sind aber nicht zitierbar und belegen keine Lückenlosigkeit; den Schutz übernimmt die Rechteprüfung.
- **Nummern aus `events` ableiten:** `events` ist ein allgemeines Ereignisprotokoll ohne Eindeutigkeitsgarantie je Fall. Teamwarnungen und KI-Fälle schreiben dort nicht zuverlässig hinein. Als Nummernquelle ist es deshalb ungeeignet.

---

## 15. Umsetzungsroadmap (jede Phase ein eigener PR, jeweils nach Freigabe)

| Phase | Inhalt | Daten | Abhängigkeit |
|---|---|---|---|
| **0a** | KI-bestätigte Bürgermaßnahmen legen über `pulse_mod_cases.create_case` einen BM-Fall an (mit DM-Einspruchsweg wie `/mod`) | additive Zeilen in `mod_cases` | – |
| ~~0b~~ | entfällt: `ai_moderation_cases.json` ist bereits im Voll-ZIP (Befund 2 widerlegt) | – | – |
| **0c** | `case_number()` mit `BEGIN IMMEDIATE` und Wiederholung bei `UNIQUE`; Parallelitätstest | – | – |
| **1** | `pulse_case_numbers.py` und Migration `case_registry_v1`; noch **ohne** Aufrufer (unschädlich); Tests aus §13 | 2 neue Tabellen | E1, E6 |
| **2** | Vergabe in den BM- und TF-Anlegestellen in derselben Transaktion; Anzeige in DM und Antworten; `parse_ref`/`resolve` verstehen `PF-` | neue Registerzeilen | 1 |
| **3** | TW mit `reserved`/`confirm`/Abgleich; Integritätsbericht im Admin-Center | neue Registerzeilen | 2, E4 |
| **4** | Backfill-Werkzeug mit Trockenlauf, Admin-Bestätigung, Audit | Registerzeilen für Altfälle | 3, E5 |
| **5** | Audit #11 auf „Fertig“, Doku (`ROUTEN`, `JOBS_UND_RECHTE`, `DATENLANDKARTE`, CHANGELOG) | – | 4 |

Phase 0 ist ohne Entscheidung über #11 sinnvoll. Phasen 1–5 setzen E1 = B voraus.
