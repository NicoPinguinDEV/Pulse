# Phase 15.2 – Melonly Runtime-Repository

Stand: 02.11.2026

## Aktiver Stand

Die aktiven Melonly-/Roblox-Datenpfade verwenden `MelonlyRepository`. Der Adapter kapselt JSON und SQLite unter demselben Aufrufvertrag. **JSON bleibt das Standard-Backend.** PR #126 führt weder eine Live-Migration noch eine automatische Aktivierung aus.

Verbundene Datenpfade:

- Dashboard-Zähler und Melonly-Seite in `pulse_pro.py`
- Create, Edit und Delete über `/log/create`, `/log/edit`, `/log/delete`
- CSV-Export über `/export/logs`
- Roblox-Vorprüfung über `/api/roblox-user`
- Roblox-Spielervorschläge über `/api/roblox-search`
- Globale Suite-Suche für Melonly-Treffer

Die Roblox-Endpunkte behalten ihre vorhandene `can_access_melonly(ctx)`-Prüfung. Die globale Suche verwendet dieselbe Prüfung, bevor sie Melonly-Datensätze abfragt.

## Backend-Auswahl

Die Variable `PULSE_LOGS_BACKEND` akzeptiert nur:

| Wert | Verhalten |
|---|---|
| nicht gesetzt | JSON, sofern Metadaten nicht SQLite als aktiv markieren |
| `json` | JSON ausdrücklich auswählen; falls SQLite aktiv markiert ist, kontrollierter Rückexport und Umschalten auf JSON |
| `sqlite` | SQLite nur benutzen, wenn `pulse.db` bereits `active_backend=sqlite` enthält |

Die Umgebungsvariable allein aktiviert SQLite **nicht**. Ist SQLite laut Metadaten aktiv, aber `PULSE_LOGS_BACKEND` nicht ausdrücklich `sqlite`, startet Pulse fail-closed, statt still auf eine möglicherweise veraltete `logs.json` zurückzufallen.

Die Runtime:
- erstellt keine Melonly-Schema-Tabellen,
- führt keine Migration aus,
- setzt keinen Backend-Marker,
- importiert keine JSON-Daten automatisch,
- verändert keine Daten bei normaler JSON-Auswahl.

Die Metadaten werden vor dem Start lesend geprüft. Ein fehlendes oder unbekanntes Schema führt nicht zu einer stillen Neuinitialisierung.

## Kontrollierter späterer SQLite-Wechsel

Ein späterer Wechsel ist ein separater, geplanter Wartungsvorgang und keine normale Deployment-Aktion:

1. Bot und Dashboard sauber stoppen; Schreibzugriffe müssen beendet sein.
2. Aktuelles Voll-Backup erstellen und mit `restore_test` vollständig prüfen.
3. JSON-Quelldatei und SHA-256 erfassen; Migration nur mit dem freigegebenen CLI und expliziter Offline-Bestätigung ausführen.
4. Anzahl, Reihenfolge, vollständige Payloads, unbekannte Zusatzfelder und Quelldatei-Hash vergleichen.
5. Rollback-Test durchführen und prüfen, dass das JSON-Rückexport-Ergebnis exakt den SQLite-Payloads entspricht.
6. Erst nach separater Freigabe `active_backend=sqlite` und `PULSE_LOGS_BACKEND=sqlite` gemeinsam setzen.
7. Nach Neustart Read-only-Funktion, CRUD, Roblox-Endpunkte, globale Suche, CSV und Backup/Restore prüfen.

Diese Schritte wurden in PR #126 nicht auf dem VPS durchgeführt.

## JSON-/SQLite-Vertrag

Der Adapter erhält insbesondere:

- Reihenfolge der Einträge
- ID-Vergleich ohne implizite Umwandlung von numerischer ID in Text-ID
- Editieren des ersten passenden IDs-Eintrags
- Löschen aller Datensätze mit streng gleicher ID (auch bei Legacy-Duplikaten)
- neueste passende Einträge zuerst auf der Melonly-Seite
- Roblox-ID-/Namenshistorie und bestehende Vorschlagsreihenfolge
- Export-Reihenfolge, UTF-8-BOM, Semikolon-Trennung, Spaltenüberschriften und CSV-Formelschutz

Die neuen Repository- und Route-Tests vergleichen beide Backends auf denselben Fixture-Daten. Live-Parität wird vor einem späteren Umschalten zusätzlich mit einem echten, gesicherten Offline-Datenbestand geprüft.

## Backup und Restore

Das Voll-Backup hält `pulse.db` und `logs.json` unter demselben I/O-Lock zusammen fest. Das Manifest speichert SQLite-Integrität, `user_version`, Melonly-Schemastatus, Backend, Generation, Revision, Importanzahl, Quellhash und Datensatzanzahl.

Beim Backup-Check wird der deklarierte Wert mit der tatsächlichen archivierten Datenbank verglichen. Der Test-Restore und ein echter Restore prüfen die Metadaten nach dem Zurückspielen erneut. Frühere Archive ohne `sqlite_metadata` bleiben lesbar. Ist SQLite aktiv, muss zusätzlich die archivierte JSON-Migrationssicherung vorhanden sein.

## Älteres Serverstruktur-Backup

Der separate JSON-Serverstruktur-Backup-Pfad nutzt beim Export jetzt ebenfalls das aktive Repository und speichert dessen Backend-Metadaten. Dadurch wird bei einem SQLite-Betrieb nicht versehentlich eine veraltete `logs.json` als aktueller Datenstand archiviert.

Der zugehörige alte Restore kann die Tabellen `melonly_logs` und `melonly_logs_meta` jedoch nicht zurückspielen. Er wird deshalb bei aktivem SQLite **vor jeder Discord- oder Pulse-Änderung abgebrochen**. Für einen SQLite-Stand ist ausschließlich das vollständige ZIP-Backup mit Datenbank-Snapshot, Manifest-Prüfung und Restore-Verifikation freigegeben. Bei JSON-Betrieb bleibt der bisherige Serverstruktur-Restore verfügbar.

## Sicherheitsgrenzen

- Der SQLite-Adapter setzt keine neue Berechtigung durch: alle bisherigen Roblox-API- und Dashboard-Gates bleiben erhalten.
- Die globale Suche liefert Melonly-Datensätze nur für Benutzer mit Melonly-Leserecht.
- Voll-Backups enthalten sensible personenbezogene Daten; POSIX-Modi sind deshalb 0700 für den Backup-Ordner und 0600 für das Archiv.
- Archive sind derzeit nicht zusätzlich verschlüsselt. Externe Backups benötigen einen separaten Verschlüsselungs- und Schlüssel-Wiederherstellungsprozess.
