# Phase 10 – Performance & Skalierbarkeit (Pulse 7.17.0)

Stand: 31.10.2026 · Branch `phase10-performance` · Basis `main@54f2a8a` (Merge von PR #89) · Python 3.14.6 lokal (Windows 11)

Grundsatz wie in Phase 8/9: **nur gemessene bzw. technisch nachweisbare Probleme beheben**, keine Funktion entfernen,
keine Schema- oder API-Änderung. Jede Änderung hat einen Test, der auf `main` fehlschlägt (Gegenprobe:
`tests/test_phase10_performance.py` auf `54f2a8a` → 3 Fehlschläge, 2 Fehler von 7 Tests der ersten Fassung).

## 1. Ausgangszustand und Messmethode

**Git:** `main` = `54f2a8a`, PR #89 gemergt, alle CI-Checks grün (Details §8). Arbeitsbaum sauber bis auf drei
unversionierte lokale Dateien unter `.claude/` (nicht angefasst).

**Bereits vorhanden** (Phase 3, nicht erneut gebaut): Config-Cache über mtime (P-04), wöchentliche Stunden einmal
gruppiert (P-03), `load_shifts` ohne Schreiben (P-05), Live-Abfrage im Thread mit 15-s-Cache (P-02), Job-Registry
(P-06), `pulse_tasks.spawn` mit begrenzter Referenzmenge (P-07), Lasttest `tools/load_test.py` + manueller
CI-Workflow `perf.yml` (768 MiB / 2 CPUs).

**Messwerkzeuge (alle mit synthetischen Daten im Temp-Ordner, Fake-Guild, Fake-Login, kein Discord/OAuth/Netz):**

- `tools/perf_baseline.py` (neu): ruft **jede parameterlose GET-Seite** (135) in-process auf und misst je Request den
  Median der Antwortzeit, die Zahl der JSON-Parses (`json.load/loads`) mit geparsten Bytes und die Zahl geöffneter
  SQLite-Verbindungen; dazu Importzeit und RSS. SSE-Streams werden übersprungen, der GitHub-Abgleich ist abgeschaltet.
- `tools/load_test.py` (vorhanden): Median/p95/p99 je Route, parallele Clients, Heap-Spitze (tracemalloc), RSS.

**Datenumfang:** 300 Mitglieder (200 im Team), 20 000 Schicht-Einträge (`shifts.json` 5,4 MB), 5 000 Log- und
Audit-Einträge, 500 Bewerbungen; Lasttest zusätzlich 5 000 Tickets, 2 000 Aufgaben, 20 000 Ereignisse.

**Gültigkeit:** Alle Zahlen stammen von **einem Windows-Entwicklungsrechner**; vorher/nachher wurden nacheinander mit
identischem Messwerkzeug gemessen (Basis im separaten Worktree von `54f2a8a`). Sie sind **nicht** auf den VPS
(Linux, 2 GiB, Container 768 MiB) übertragbar. Auf dem VPS und im Linux-Container wurde **nicht** gemessen.

### Baseline (vorher, `main@54f2a8a`) – die teuersten Seiten

`tools/perf_baseline.py --runs 3 --history 20000 --members 300`, Median aus 3 Aufrufen nach einem Aufwärmaufruf;
JSON/SQLite = Zählung im letzten Aufruf.

| Seite | ms | JSON-Parses / MB | SQLite-Verbindungen |
|---|---:|---:|---:|
| `/ultimate/achievements` | 15 954 | 605 / 1 062,8 | 1 410 |
| `/ultimate/team-trends`, `/reports`, `/reports.csv`, `/scoreboard` | 3 371 – 3 600 | 5 / 5,3 | 1 006 – 1 008 |
| `/ultimate/api/team`, `/metrics`, `/export.json`, `/team`, `/analytics` | 2 780 – 2 832 | 5–6 / 5,3–5,6 | 805 – 809 |
| `/team` | 520 | 205 / 62,4 | 8 |
| Summe der Mediane aller 135 Seiten | 47 038 | | |

Prozess: Import `webserver` 2,8 s; RSS 22 → 100 MB nach Import → 143 MB nach allen Seiten.

## 2. Gefundene Probleme

| ID | Priorität | Befund (nachgewiesen) | Ursache |
|---|---|---|---|
| P10-01 | Kritisch (CPU, Skalierung) | `/ultimate/achievements` brauchte 14,8 s, parste **605×** JSON (**≈ 1 GB**) und öffnete 1 410 SQLite-Verbindungen je Aufruf. | `award_achievements(m)` ohne Statistik rief für **jedes** Mitglied `member_stats(m)` → `stats_context()` neu auf (alle JSON-Dateien + 5 000 Tickets je Mitglied); danach je Mitglied eine eigene COUNT-Verbindung. Lief zudem synchron auf dem Event-Loop des Bots. |
| P10-02 | Hoch (DB, Skalierung) | Alle Teamauswertungen (`/ultimate/team`, `/api/team`, `/metrics`, `/analytics`, `/export.json`, `/reports(.csv)`, `/scoreboard`, `/team-trends`, Housekeeping-Scores) öffneten **≈ 4 Verbindungen je Mitglied** (805–1 008 je Seite) und filterten die gesamte Schicht-Historie einmal **pro Mitglied** (O(Mitglieder × Historie)). | `member_stats()` las Aufgaben, Trainingsversuche, Profil und (über `ensure_profile`) das Profil erneut je Mitglied mit eigener Verbindung. |
| P10-03 | Mittel (DB) | `/ultimate/reports`, `/scoreboard`, `/team-trends`, `/reports.csv`: zusätzlich 1 Verbindung je Mitglied. | `all_stats()` → `record_score()` prüfte je Mitglied einzeln, ob heute schon ein Snapshot existiert. |
| P10-04 | Mittel (IO) | `/team` parste **205×** JSON (62 MB) je Aufruf. | `team_since_for()` las `applications.json` für jedes Mitglied ohne gespeichertes `team_since` neu. |
| P10-05 | Mittel (Event-Loop) | `all_stats()` (SQLite/JSON für alle Teammitglieder) lief in vier `async`-Handlern direkt auf dem Event-Loop, den Bot und Dashboard teilen. | Fehlendes Auslagern (bei `/ultimate/team` seit M-C1 bereits per `asyncio.to_thread`). |
| P10-07 | Mittel (Datensicherheit lokal) | `tools/preview_server.py` (E2E), `tools/load_test.py`: `/team` und `/activity-check` öffneten bzw. **erzeugten `activity_check.db` im Checkout** (beim Messen im frischen Worktree entdeckt). | `webserver.ACTIVITY_DB` wird beim Import aus dem Repository-Pfad berechnet; die Tools setzten nur `BASE_DIR`. |
| P10-06 | Niedrig (Messbarkeit) | `tools/load_test.py` meldete unter 64-bit-Windows nie RSS („None“). | Pseudo-Handle von `GetCurrentProcess` ohne `restype` auf 32 Bit gekürzt → `GetProcessMemoryInfo` schlug fehl. |

## 3. Durchgeführte Änderungen

| Datei | Änderung |
|---|---|
| `pulse_db.py` | `task_totals_by_assignee(1000)` und `passed_attempts_by_user(200)`: je **eine** Abfrage mit `ROW_NUMBER() OVER (PARTITION BY …)` – gleiche Sortierung und gleiche Obergrenze wie die bisherigen Einzelabfragen (`list_tasks(..., limit=1000)`, `attempts(..., limit=200)`). Sortierausdruck von `list_tasks` als gemeinsame Konstante `_TASK_ORDER` (Text unverändert). |
| `pulse_ultimate.py` | `stats_context()` liefert zusätzlich: Historie einmal nach Mitglied gruppiert, Aufgaben-/Trainings-Summen, alle Profile (`all_profiles()`). `member_stats()` nutzt diese Werte, wenn vorhanden – sonst unverändert der alte Pfad (ältere/eigene Kontexte). `ensure_profile` wird weiterhin aufgerufen, sobald ein Profil fehlt, umbenannt oder archiviert ist (gleiche Bedingung wie sein „nichts zu schreiben“-Pfad). Defekte Historien-Einträge: Rückfall auf den alten Filter (gleicher Fehler wie bisher). |
| `pulse_next.py` | `/ultimate/achievements`: ein `stats_context` je Seite, Vergabe über **eine** Verbindung (`award_achievements(..., conn=)`, gleiche `INSERT OR IGNORE`), eine `GROUP BY`-Zählung; Berechnung im Worker-Thread. `all_stats()`: heutige Snapshots einmal vorab gelesen (`_scored_today`), `record_score` nur noch für Mitglieder ohne Snapshot (vorher kehrte es für diese sofort mit `False` zurück). Die vier `all_stats`-Seiten rechnen per `asyncio.to_thread`. |
| `webserver.py` | `team_since_for(member, entry, apps=None)`: optional bereits geladene Bewerbungen bzw. ein Callable; `/team` lädt `applications.json` höchstens einmal je Seite und nur, wenn ein Mitglied kein `team_since` hat. Aufruf ohne `apps` liest wie bisher. |
| `tools/perf_baseline.py` | Neues Messwerkzeug (siehe §1), gleicher Isolations-Guard wie Preview/Lasttest. |
| `tools/load_test.py` | RSS unter 64-bit-Windows (P10-06). |
| `tools/preview_server.py`, `tools/load_test.py`, `tools/perf_baseline.py` | `ws.ACTIVITY_DB` ins Temp-Verzeichnis (P10-07); `tests/test_tool_isolation.py` verlangt das jetzt für alle drei Tools. |
| `tests/feature_smoke.py` | Zwei Quelltext-Asserts auf die erweiterten Signaturen (`award_achievements(..., conn=None)`, `team_since_for(..., apps=None)`). |
| `tests/test_phase10_performance.py` | 9 Regressionstests (§5). `tests/test_tool_isolation.py` prüft auch das neue Tool. |
| `docs/PHASE9_BERICHT.md`, `docs/PHASE9_REVIEW.md` | Nachtrag mit geprüftem Merge-/CI-Status (§8). |

Keine Änderung an Routen, Rechten, Slash-Commands, Tabellen, JSON-Schlüsseln oder Antwortformaten. Keine neuen
Hintergrundtasks, Caches oder Queues; die Vorab-Daten leben nur für einen Seitenaufruf.

## 4. Messwerte vorher/nachher

Gleicher Rechner, gleiche Daten, gleiches Messwerkzeug, nacheinander (Basis-Worktree `54f2a8a`, danach `phase10-performance`).

### Seitenmessung (`tools/perf_baseline.py`, 300 Mitglieder, 20 000 Schichten)

| Seite | ms vorher | ms nachher | JSON vorher (Anzahl/MB) | JSON nachher | SQLite vorher | SQLite nachher |
|---|---:|---:|---:|---:|---:|---:|
| `/ultimate/achievements` | 15 954 | 90 | 605 / 1 062,8 | 5 / 5,3 | 1 410 | 7 |
| `/ultimate/team-trends` | 3 600 | 78 | 5 / 5,3 | 5 / 5,3 | 1 008 | 7 |
| `/ultimate/reports` | 3 371 | 92 | 5 / 5,3 | 5 / 5,3 | 1 008 | 7 |
| `/ultimate/reports.csv` | 3 399 | 75 | 5 / 5,3 | 5 / 5,3 | 1 006 | 5 |
| `/ultimate/scoreboard` | 3 507 | 77 | 5 / 5,3 | 5 / 5,3 | 1 007 | 6 |
| `/ultimate/api/team` | 2 820 | 75 | 5 / 5,3 | 5 / 5,3 | 805 | 4 |
| `/ultimate/metrics` | 2 804 | 91 | 5 / 5,3 | 5 / 5,3 | 805 | 4 |
| `/ultimate/export.json` | 2 832 | 79 | 5 / 5,3 | 5 / 5,3 | 806 | 5 |
| `/ultimate/team` | 2 780 | 79 | 5 / 5,3 | 5 / 5,3 | 809 | 6 |
| `/ultimate/analytics` | 2 831 | 83 | 6 / 5,6 | 6 / 5,6 | 808 | 7 |
| `/team` | 520 | 222 | 205 / 62,4 | 5 / 5,6 | 8 | 8 |
| **Summe der Mediane aller 135 Seiten** | **47 038** | **3 549** | | | | |

- **Kein Statuscode geändert** (alle 135 Seiten gleich). Keine Seite parst mehr JSON oder öffnet mehr Verbindungen –
  einzige Ausnahme `/admin/panels` (5 → 8): **Messartefakt**. `pulse_panels` liest die Cog-Datenbanken read-only aus
  dem Repository-Ordner, und nur der Haupt-Checkout enthält lokale `.db`-Dateien (gegengeprüft: auf beiden Ständen
  reproduzierbar, mit isoliertem Pfad verschwindet der Unterschied). Das Messwerkzeug leitet diesen Pfad jetzt um.
- Kleinere Laufzeitzunahmen ≤ 16 ms (`/warns.csv` 72 → 89 ms, `/applications` 250 → 264 ms) betreffen nicht geänderten
  Code und liegen im Rauschen eines Desktop-Rechners.
- Prozess: Import 2,8 → 2,3 s, RSS nach allen Seiten 143 → 142 MB – **keine Speicheränderung nachweisbar**.

### Lasttest (`tools/load_test.py`, 300 Mitglieder, 5 000 Tickets, 2 000 Aufgaben, 20 000 Ereignisse)

| Route | 1 Client, Median (ms) | 4 parallele Clients, Median (ms) | 4 Clients, p95 (ms) |
|---|---:|---:|---:|
| `/ultimate/team` | 3 128 → 120 | 15 577 → 448 | 17 961 → 7 277 |
| `/team` | 468 → 504 | 1 822 → 1 798 | 2 059 → 2 175 |
| `/dashboard` | 116 → 125 | 494 → 500 | 631 → 588 |
| `/ultimate` | 98 → 114 | 443 → 439 | 493 → 549 |
| Fehler | 0 → 0 | 0 → 0 (156 Anfragen) | |
| Heap-Spitze (tracemalloc) | 84,5 → 84,5 MB | 99,2 → 115,3 MB | |
| RSS am Ende | – | 208 → 209 MB | |

- `/ultimate/team`: p95/Max bleibt hoch, weil der **erste** Aufruf für 200 neue Mitglieder je ein Profil anlegt
  (eine Schreibtransaktion je Mitglied, in beiden Ständen gleich); danach ~120 ms.
- `/team` im Lasttest ohne Verbesserung: die Lasttest-Daten enthalten keine `applications.json`, P10-04 greift dort
  nicht (Seitenmessung mit 500 Bewerbungen: 520 → 222 ms). Unterschiede ±10 % bei unveränderten Seiten = Rauschen.
- Heap-Spitze bei 4 parallelen Clients +16 MB, RSS am Ende unverändert. Ursache nicht eindeutig zugeordnet (möglich:
  die Teamseiten laufen jetzt echt parallel in Worker-Threads, jede mit eigenem Vorab-Kontext). Weit unter den
  Schwellen 500/620/700 MB, aber als Beobachtungspunkt notiert.
- **Event-Loop (P10-05):** technisch begründet (Arbeit läuft per `asyncio.to_thread`, wie bei `/ultimate/team` seit
  M-C1); die Loop-Verzögerung wurde nicht separat gemessen.

## 5. Tests (tatsächlich ausgeführt, frischer Worktree auf `8b60062`, Windows, Python 3.14.6)

| Prüfung | Ergebnis |
|---|---|
| `python -m ruff check .` | All checks passed |
| `python -m compileall -q .` | OK |
| `tests/feature_smoke.py` | OK (zwei Quelltext-Asserts auf die erweiterten Signaturen nachgezogen) |
| `tests/smoke_test.py` | OK |
| `unittest discover -s tests -p 'test_*.py'` | **1132 Tests OK**, 13 übersprungen (12 E2E ohne `PULSE_E2E`, 1 Linux-only SIGTERM) |
| E2E (`PULSE_E2E=1 … tests.test_e2e_dashboard`, Playwright/Chromium) | **15 OK** |
| CI-Leckprüfung (`git status --ignored` auf JSON/DB/Secret/Logs) | keine Dateien |
| Neue Tests `tests/test_phase10_performance.py` | 9 OK; Gegenprobe der ersten 7 auf `main`: 3 Fehlschläge + 2 Fehler |

Die neuen Tests prüfen: identische `member_stats`-Werte zwischen neuem und altem Pfad (inkl. > 1 000 Aufgaben und
> 200 Trainingsversuchen eines Mitglieds, Altformate der Historie), Profil-Update bei Umbenennung, Rückfall bei defekten
Historien-Einträgen, konstante Verbindungszahl unabhängig von der Teamgröße, ein `stats_context` je
Achievements-Seite mit identischen freigeschalteten Achievements, `record_score` nur ohne heutigen Snapshot
(ein Snapshot je Mitglied und Tag), die vier ausgelagerten Seiten liefern 200 mit allen Mitgliedern,
`applications.json` einmal je `/team`, `team_since_for` ohne `apps` wie bisher.

**Nicht ausgeführt:** Linux-CI und Docker-Build (laufen erst nach einem Push), Messungen auf dem VPS, `perf.yml`.

## 6. Bekannte Einschränkungen

- **Gleichstand an der Obergrenze:** Hat ein Mitglied mehr als 1 000 Aufgaben bzw. 200 Trainingsversuche und liegen an
  der Grenze Einträge mit identischem Sortierschlüssel, entscheidet SQLite die Reihenfolge – bei der alten
  Einzelabfrage ebenso, aber nicht zwingend gleich. Nur dann könnte eine Grenzzeile anders gezählt werden.
- **Profil-Momentaufnahme:** Innerhalb **eines** Seitenaufrufs gilt der Profilstand vom Beginn der Berechnung (vorher
  je Mitglied wenige Millisekunden später gelesen). Geänderte Namen/Archivierung werden wie bisher geschrieben.
- Alle Zeiten stammen von Windows; der VPS (Linux, 2 GiB, 768-MiB-Container) wurde nicht gemessen.
- `/ultimate/achievements` schreibt Achievements weiterhin beim Seitenaufruf (bestehendes Verhalten, nicht geändert).

## 7. Geprüft ohne Änderungsbedarf

- **Caches/Queues:** alle modulweiten Caches sind begrenzt oder TTL-basiert (`_roblox_cache` mit Obergrenze,
  `_EVENT_SEEN` 2 000, `_VERIFY_CACHE` 60, DM-Queue `deque(maxlen=…)`, KI-Moderation `asyncio.Queue(maxsize=…)`,
  Dedupe-Dicts der Cogs begrenzt). `cogs/recovery.py` `_last_sync_by_user` wächst nur je Admin, der `/recovery` nutzt.
- **Hintergrundjobs:** 13 `@tasks.loop` (8 s, 2 × 1 min, 7 × 5 min, 15 min, Poll-Intervall, täglich 06:00) – unverändert,
  Bestand laut P0-4/P-06. Phase 10 fügt **keinen** Task, Loop, Cache oder Thread-Pool hinzu.
- **HTTP-Pools:** alle `httpx`-Clients mit `Limits` (2–5 Verbindungen) und Timeouts.
- **Discord-Rate-Limits:** keine Änderung an Discord-Aufrufen.

## 8. Phase-9-Dokumentation (geprüft und aktualisiert)

`docs/PHASE9_BERICHT.md` (§7 und neuer §8) sowie `docs/PHASE9_REVIEW.md` (Vorschläge und §7) beschrieben den Stand vor dem
Push. Mit `gh` geprüft: PR #89 mit Head `3124d2d` am 31.10.2026 um 19:53 UTC gemergt (Merge-Commit `54f2a8a`), alle
11 PR-Checks erfolgreich, Workflows auf `main` erfolgreich, Linux-Unittests 1123 OK (15 übersprungen; der echte
SIGTERM-Test nicht, seine einzige Skip-Bedingung ist Windows). Deploy-Workflow übersprungen → **kein Deployment**.

## 9. Offene Punkte / Empfehlungen

1. **`perf.yml` im Linux-Container (768 MiB, 2 CPUs) laufen lassen** – manuell, nach Freigabe; erst dann gibt es
   VPS-nahe Zahlen. Die Heap-Spitze bei Parallelität dort beobachten.
2. **`shifts.json` wächst unbegrenzt (D-07/A-05):** wird auf `/dashboard` und `/stats` zweimal, sonst je Seite einmal
   vollständig geparst (5,4 MB bei 20 000 Einträgen). Nächster großer Hebel: Historie nach SQLite oder ein mtime-Cache
   der geparsten Historie – beides Entscheidungen zum Datenmodell mit Migration, deshalb hier nicht umgesetzt.
3. **Weitere synchrone Seiten auf dem Event-Loop:** `/team` (~220 ms), `/cockpit` (~390 ms), `/applications`
   (~260 ms) bei 300 Mitgliedern – Kandidaten für `asyncio.to_thread` nach demselben Muster.
4. **`/ultimate/team` beim ersten Aufruf:** Profile neuer Mitglieder werden einzeln angelegt (eine Transaktion je
   Mitglied) – ließe sich bündeln.
5. **Dev-Tools/E2E:** `pulse_panels` liest die Cog-Datenbanken read-only aus dem Repository-Ordner, auch im
   Preview-Server (E2E) und im Lasttest – im Messwerkzeug umgeleitet, in den beiden anderen Tools noch offen.
6. Die doppelte Registrierung `GET /ultimate` (K-16/A-01) bleibt unverändert; auch die verdeckte Variante profitiert.

## 10. Commits

| Commit | Inhalt |
|---|---|
| `24664d1` | Phase 10: Teamauswertungen ohne Arbeit pro Mitglied (Code, Tests, Messwerkzeug, Phase-9-Nachtrag) |
| `8b60062` | Dev-Tools öffnen `activity_check.db` nicht mehr im Checkout; Smoke-Asserts nachgezogen |
| Bericht-Commit | `docs/PHASE10_BERICHT.md` |

Branch `phase10-performance`, **nicht gepusht, nicht gemergt, nicht deployt.** Keine Produktivumgebung berührt.
