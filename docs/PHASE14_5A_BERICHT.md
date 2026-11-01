# Phase 14.5a – Housekeeping Error Observability

**Basis:** `e7d24c52f9288f6d6bf0daadb38c3ad3c9d4735b`  
**Branch:** `phase/14-5a-housekeeping-error-observability`

## Änderungen

Die aktive `Housekeeping (5 min)`-Schleife verwendet jetzt `pulse_jobs.record_error`, wenn ein Teilbereich intern fehlschlägt. Das gilt für Warn-/Probezeit-Prüfung, Schichtkontrolle, Benachrichtigungsbereinigung, Retention, überfällige Bewerbungen, LOA-Erinnerungen und Presence-Aktualisierung.

Auch Fehler beim Laden der Teammitglieder-/Managerliste werden nicht mehr verschluckt.

Der Fehler wird unter dem bestehenden Job-Namen `Housekeeping (5 min)` registriert. Dadurch erscheint er in `/healthz` bzw. in der Systemübersicht über `pulse_jobs.health_summary()`. Der bestehende Alert-Cooldown bleibt zuständig, damit Discord nicht mit Warnungen überflutet wird.

Die Schleife läuft nach einem Fehler in einem einzelnen Teilbereich weiter. Der zentrale `@tracked`-Wrapper setzt `last_ok` für diesen Durchlauf nicht, wenn währenddessen ein Fehler registriert wurde.

## Regressionstest

Ein Test löst gezielt einen Fehler in der Schichtkontrolle aus und prüft:
- Fehler erscheint in `jobs_failing`.
- Der Jobdurchlauf wird weiterhin gezählt.
- Nachfolgende Housekeeping-Schritte (Presence-Aktualisierung) werden trotzdem ausgeführt.

## Sicherheit und Umfang

- Keine Intervall-, Zeitfenster-, Deduplizierungs- oder Aufbewahrungsregeln geändert.
- Keine neuen Hintergrundthreads, Scheduler oder Endlosschleifen.
- Keine Discord-Aktionen außerhalb des vorhandenen Housekeeping-Verhaltens.
- Keine DB-/JSON-Schemaänderung und keine Laufzeitdatenmigration.
- Das Job-Inventar bleibt mit der statischen Analyse synchron.
