# Phase 15.4 – Melonly 100K Performance-Nachweis

**Messdatum:** 02.11.2026  
**Workflow:** [Melonly Storage Benchmark – Run #3](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41431012828)  
**Commit:** `0317a637932c1030e32aa824674ba47ccbdf3795` (PR #129)  
**Branch beim Lauf:** `fix/melonly-benchmark-pythonpath-pipefail`  
**Parameter:** 20.000 und 100.000 Einträge · 3 Wiederholungen · 2 vCPU · 2 GB Containerlimit  
**Daten:** ausschließlich synthetisch · keine Produktionsdaten  
**Paritätsprüfungen:** 20K bestanden · 100K bestanden  
**Produktiv-Backend:** JSON; durch den Benchmark nicht verändert

## Ergebnis

Der reparierte Workflow wurde erfolgreich ausgeführt. Der Python-Import funktionierte mit `PYTHONPATH=/app`; `set -Eeuo pipefail` stellt sicher, dass ein Python-/Docker-Fehler nicht mehr durch `tee` als erfolgreicher Schritt verborgen wird. PR #129 ist inzwischen in `main` gemergt.

### Laufzeit – 100.000 Einträge

Median aus jeweils drei Wiederholungen, Angaben in Millisekunden.

| Operation | JSON | SQLite | Schneller |
|---|---:|---:|---|
| count | 0,005 | 1,129 | JSON |
| count_by_type | 64,469 | 6,475 | SQLite |
| latest_100 | 0,243 | 0,550 | JSON |
| latest_matching_types_100 | 0,410 | 343,234 | JSON |
| find_first_by_id | 0,010 | 0,239 | JSON |
| player_history_by_name | 0,093 | 0,805 | JSON |
| player_history_by_roblox_id | 0,095 | 0,815 | JSON |
| roblox_user_history | 0,076 | 0,411 | JSON |
| roblox_suggestions_1000 | 2,393 | 3,562 | JSON |
| search_payload_unique | 7,725 | 813,617 | JSON |
| export_all | 333,271 | 456,827 | JSON |
| iter_all_500 | 292,074 | 432,647 | JSON |

### Laufzeit – 20.000 Einträge

| Operation | JSON | SQLite |
|---|---:|---:|
| count | 0,006 | 0,375 |
| count_by_type | 12,658 | 1,473 |
| latest_100 | 0,246 | 0,580 |
| latest_matching_types_100 | 0,408 | 65,979 |
| find_first_by_id | 0,011 | 0,238 |
| player_history_by_name | 0,031 | 0,557 |
| player_history_by_roblox_id | 0,031 | 0,552 |
| roblox_user_history | 0,021 | 0,266 |
| roblox_suggestions_1000 | 2,382 | 3,539 |
| search_payload_unique | 1,608 | 162,589 |
| export_all | 49,982 | 87,051 |
| iter_all_500 | 55,425 | 80,938 |

### Speicher und Dateigröße

| Messwert | 20.000 | 100.000 |
|---|---:|---:|
| JSON-Datei | 5.228.891 Byte | 26.188.891 Byte |
| SQLite-Datei | 9.998.336 Byte | 53.329.920 Byte |
| SQLite-Import | 341,449 ms | 1.846,181 ms |
| Prozess-RSS aktuell | 84,4 MB | 317,6 MB |
| Prozess-RSS Peak | 114,9 MB | 501,5 MB |
| Container aktuell | 87,3 MB | 385,1 MB |
| Container Peak | 118,0 MB | 567,0 MB |
| Containerlimit | 2.048 MB | 2.048 MB |

## Bewertung

1. **JSON bleibt vorerst das Produktiv-Backend.** Bei 100K lag der gemessene Container-Peak bei 567 MB und damit innerhalb des für Pulse dokumentierten 768-MiB-RAM-Budgets. Dieser synthetische Runner-Messwert ist kein VPS-Live-Messwert.
2. **SQLite ist bei `count_by_type` klar schneller.** Der JSON-Pfad benötigt 64,469 ms gegenüber 6,475 ms bei SQLite. Zu prüfen ist, ob der JSON-Repository-Cache eine inkrementelle Typ-Aggregation mit korrekter Invalidierung bereitstellen kann.
3. **SQLite ist bei den getesteten Suchpfaden nicht generell schneller.** `search_payload_unique` benötigt 813,617 ms und `latest_matching_types_100` 343,234 ms. Eine Backend-Umschaltung wäre allein aufgrund der vorhandenen B-Tree-Indizes nicht begründet.
4. **Keine Daten- oder Backendänderung.** Der Lauf verwendet temporäre synthetische Datensätze und ändert weder `PULSE_LOGS_BACKEND` noch `active_backend`.

## Folgearbeiten

- `count_by_type` im JSON-Pfad nur nach Profiling und mit Cache-Invalidierungs-/Paritätstests optimieren.
- Für SQLite-Suche Query-Plan und Fallback-Verhalten gezielt untersuchen; keine ungetestete FTS-/Trigramm-Migration.
- Phase 15.5: vollständiger 100K-Backup-/Restore-Rehearsal, SQLite-Metadaten-/Integritätsprüfung, Abbruchwiederherstellung und JSON-Rückexport.

## Einschränkung des Nachweises

Der Benchmark belegt Ergebnisgleichheit und Laufzeiten der getesteten Repository-Operationen auf einem GitHub-Runner im 2-vCPU-/2-GB-Container. Er beweist weder die reale VPS-Latenz noch die Performance bei parallelen Dashboard-Anfragen oder gleichzeitigen produktiven Schreibvorgängen. Ein VPS-Test fand nicht statt.
