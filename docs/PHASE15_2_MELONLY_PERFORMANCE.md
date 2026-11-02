# Phase 15.2 – Melonly-Performance-Baseline

**Status:** Benchmark-Werkzeug vorbereitet · Messlauf noch nicht ausgeführt  
**Sicherheit:** ausschließlich synthetische Daten · kein Produktiv-Backend-Wechsel · keine VPS-Daten

## Ziel

Vor weiteren Änderungen an den Melonly-Abfragen werden JSON und SQLite auf identischen Datensätzen verglichen. Der Benchmark misst Laufzeiten, Datenbank-/Quelldateigröße und prüft vor jeder Messung, dass beide Backends exakt dieselben Ergebnisse liefern.

## Messumfang

- 20.000 und 100.000 synthetische Datensätze
- Datensatzanzahl und Zähler je Aktionstyp
- neueste Einträge und neueste Einträge bestimmter Aktionstypen
- ID-Auflösung
- Roblox-Spielerhistorie über Namen und Roblox-ID
- Roblox-API-Verlauf und Spielervorschläge
- globale Payload-Suche
- vollständiger Export und iterativer Export in 500er-Batches

Jede Abfrage wird vor der Zeitmessung auf Ergebnisgleichheit geprüft. Die Ausführungszeiten sind Medianwerte aus mehreren Wiederholungen. JSON und SQLite laufen auf demselben Rechner mit denselben Datensätzen.

## Ausführen

Der manuelle GitHub-Workflow **Melonly Storage Benchmark** baut das Produktionsimage und führt den Benchmark mit 2 vCPUs und 2 GB RAM aus:

Actions → Melonly Storage Benchmark → Run workflow

Standardwerte: 20.000 und 100.000 Datensätze und 3 Wiederholungen. Die Eingaben sind auf höchstens 100.000 Datensätze je Messgröße und 10 Wiederholungen begrenzt.

Lokal kann derselbe Lauf mit der Projekt-Python-Umgebung erfolgen:

    python tools/melonly_bench.py --entries 20000,100000 --repeats 3

## Interpretation

- Eine Messung mit fehlgeschlagener Paritätsprüfung wird abgebrochen und darf nicht als Performance-Ergebnis verwendet werden.
- Ein Verhältnis json_to_sqlite_ratio > 1 bedeutet, dass SQLite für diese Abfrage schneller war; unter 1 bedeutet, dass JSON schneller war.
- GitHub-Runner und VPS sind unterschiedliche Systeme. Vorher-/Nachher-Vergleiche nur auf derselben Hardware und mit denselben Parametern durchführen.
- Die Messung ändert weder PULSE_LOGS_BACKEND noch active_backend. JSON bleibt produktiv aktiv.
- Erst nach einer dokumentierten Baseline werden gezielte Index-/Abfrageoptimierungen umgesetzt und erneut gemessen.

## Nächster Schritt

Die erste Baseline ausführen, auffällige Abfragen identifizieren und die SQLite-Indizes gezielt gegen die tatsächlichen Query-Pläne prüfen. Danach folgt ein separater Optimierungs-PR mit Paritätstests und einer kontrollierten Schemaänderung, falls zusätzliche Indizes erforderlich sind.
