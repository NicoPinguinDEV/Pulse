# Pulse 5.1.1 – SQLite Hotfix

## Fehler
Beim Start konnte eine bestehende `pulse.db` mit einem älteren `notifications`-Schema mit
`sqlite3.OperationalError: no such column: dedupe_key` abstürzen.

## Ursache
Der Index `idx_notifications_dedupe` wurde innerhalb des `executescript()` erstellt, bevor die
vorhandene Tabelle durch `_ensure_column()` um `dedupe_key` erweitert wurde.

## Lösung
Der Index wird erst nach allen Migrationen angelegt. Bestehende Datenbanken bleiben erhalten und
werden beim Start automatisch ergänzt.

## Deployment
Die vorhandene `pulse.db` nicht löschen. Nur den Code aktualisieren und den Bot neu starten.
