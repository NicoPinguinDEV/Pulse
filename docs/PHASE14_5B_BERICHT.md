# Phase 14.5b – Zentrale Verwaltung einmaliger Hintergrundtasks

**Basis:** `91a66bf9b43bb724da1cebf5c3420d172a240f07`  
**Branch:** `phase/14-5b-managed-background-tasks`

## Änderungen

Die bestehenden einmaligen Hintergrundtasks verwenden nun die vorhandene zentrale Funktion `pulse_tasks.spawn`:

- Webserver und SIGTERM-Shutdown
- Auto-Backup im FastAPI-Lifespan
- Pulse Ultimate Automation Engine
- KI-Moderationsworker und Ausfallbenachrichtigung
- Serverstatistik-Refresh und verzögerter Refresh
- explizit bestätigter Recovery-Gesamtsync
- Teamlisten-Debounce
- bestehende Mitglieder-Namenspräfix-Synchronisierung
- Advanced-Logs-Flush

Die bisherigen Task-Attribute bleiben bestehen. `cog_unload`, Shutdown, Cancellation und Statusabfragen behalten ihre Handles und ihr bisheriges Verhalten. Der zentrale Manager hält zusätzlich starke Referenzen und meldet unbehandelte Task-Fehler.

Kernaufgaben sind als `critical=True` markiert und werden nicht an der weichen Kapazitätsgrenze verworfen. Die bereits zentral verwalteten Voice-Support- und Sicherheits-Tasks bleiben unverändert.

## Tests und Inventar

- Neuer Kapazitätstest: normale Tasks werden an der Grenze sauber abgelehnt und ihre Coroutine geschlossen.
- Kritische Tasks werden trotz erreichter Kapazitätsgrenze gestartet und nach Abschluss aus dem Registry-Set entfernt.
- AST-Regressionstest verhindert direkte `create_task`-/`ensure_future`-Aufrufe in den migrierten Kernmodulen.
- `tools/jobs_perms.py` inventarisiert jetzt auch lokal importierte `pulse_tasks.spawn`-Aufrufe.
- `docs/JOBS_UND_RECHTE.md` enthält die 15 bekannten zentralen Task-Startstellen.

## Grenzen und Sicherheit

- Keine neuen Scheduler, Threads oder Endlosschleifen.
- Keine Änderung an Intervallen, Datenformaten, Rollen, URLs, Discord-IDs oder `custom_id`s.
- Keine Datenbankmigration und keine Laufzeitdatenänderung.
- Recovery- und Moderations-Tasks bleiben abbrechbar und werden nicht automatisch neu gestartet.
- Kein VPS-Deployment in diesem PR.
