# Phase 14.6 – Zuverlässigkeit wiederkehrender Hintergrundjobs

**Basis:** `0ea0b46360236b8c513fc1c56d7e826ca52a767b` (Version 7.17.0)  
**Branch:** `fix/phase14.6-recurring-job-reliability`

## Kernbefund

`discord.ext.tasks` (discord.py 2.7.1) startet eine Schleife nur nach Netzwerkfehlern neu (`OSError`,
`aiohttp.ClientError`, `asyncio.TimeoutError`, `GatewayNotFound`, `ConnectionClosed`, mit eigenem Backoff).
Jede andere Ausnahme – `sqlite3.OperationalError: database is locked`, `discord.Forbidden` (403),
`discord.NotFound` (404), ein kaputter JSON-Wert – beendet den Loop **für den Rest der Prozesslaufzeit**.
`pulse_jobs.tracked` hat den Fehler zwar erfasst, aber erneut ausgelöst. Damit stand der Job nach dem ersten
nicht-netzwerkbedingten Fehler still, bis Pulse neu gestartet wurde. Kein Codepfad prüft `Loop.failed()`.

## Inventur

### `@tasks.loop` (13, alle mit `@tracked`)

| Job | Start | Intervall | Eigene Fehlerbehandlung (vorher) | Ausfallfolge vorher | Tests |
|---|---|---|---|---|---|
| Activity-Check täglich | `ActivityCheckCog.cog_load` | 06:00 Berlin | Send-Fehler nur `print` | Tagescheck fehlt, Loop tot bei DB-Fehler | neu |
| Activity-Check Nachholen | dito | 5 min | keine | Nachholen tot | neu |
| Activity-Check Warnungen | dito | 1 min | pro Guild `print`, nicht in /healthz | Warnfehler unsichtbar | neu |
| Abmeldungen Ablauf | `cog_load` | 15 min | 1 breit | Ablauf stoppt nur bei Fehler außerhalb try | vorhanden |
| TeamOS-Housekeeping | `cog_load` | 5 min | `members()`-Fehler nur `print` | unsichtbar | neu |
| Housekeeping (pulse_pro) | `cog_load` | 5 min | 11 breit, `db.notify` ungeschützt | ein Notify-Fehler überspringt Ticket-/LOA-Erinnerungen und Presence | neu |
| Suite-Automatik | `cog_load` | 5 min | 1 breiter Block um 9 Schritte, nur `print` | ein Fehler (z. B. 403) überspringt alle folgenden Schritte, unsichtbar | neu |
| Social-Promo Abruf | `cog_load` | konfig. | 2 | Loop tot bei Fehler außerhalb try | Wrapper-Test |
| Teamliste Abgleich | `cog_load` | 5 min | breit + `record_error` | ok | vorhanden |
| Tickets Panels & Automatik | `cog_load` | 5 min | 2 breit + `record_error` | ok | vorhanden |
| Verifikation Erinnerung | `cog_load` | 5 min | 1 breit | ok | – |
| Voice-Support Bereitschaft | `cog_load` | 1 min | keine | Rollenabgleich/Status tot bei DB-Fehler oder 403 | neu |
| Voice-Support Musik | `cog_load` | 8 s | nur Disconnect | Wartemusik tot | neu |

### Eigenständige Schleifen

| Schleife | Start | Pause | Fehlerbehandlung | Bewertung |
|---|---|---|---|---|
| `automation_loop` (pulse_ultimate) | `pulse_tasks.spawn(critical=True)` | 60 s | `job()` + `record_error(already_counted)`, 60 s Pause, `CancelledError` → Ende | robust; Alerts 30 min gedrosselt |
| `auto_backup_loop` (webserver) | Lifespan, `spawn(critical=True)` | 300 s vor jedem Versuch | `job()` + `_system_alert` | robust, keine Hot-Loop |
| KI-Moderationsworker `_worker` | `cog_load`, `spawn(critical=True)` | Queue (max. 10–250) | pro Element isoliert, `task_done` in `finally` | **Startwiederherstellung ungeschützt** |
| `_wait_and_publish_outage_notice` | `spawn(critical=True)` | Restzeit | breit, `finally` räumt Handle | ok |

Der Generator meldet zusätzlich `recovery._run_all_sync`. Das ist ein manuell bestätigter Einmallauf mit
äußerem `except`/`finally` (`running=False`) und kein wiederkehrender Job.

### `pulse_tasks.spawn`

Das weiche Limit liegt bei `MAX_RUNNING = 200`. `critical=True` wird nie verworfen, normale Tasks werden an der
Grenze mit `coro.close()` abgelehnt. Alle Kernschleifen sind `critical=True`. Fehler werden mit Fehler-ID
geloggt. Es gibt keine Änderung und keinen Befund.

## Gefundene Probleme und Reproduktionen

Alle Probleme sind in `tests/test_phase14_6_job_resilience.py` reproduziert. Vor dem Fix schlugen 12 von 13
Tests fehl. Der Abbruchtest bestand schon vorher und sichert das unveränderte Verhalten ab.

1. **Tracked-Loops enden dauerhaft nach einem Nicht-Netzwerkfehler.** Ein echter `tasks.loop` mit
   `sqlite3.OperationalError` lief vorher nach dem ersten Fehler nicht weiter (`failed() == True`).
2. **Fehler ohne Fehler-ID.** `job()`/`record_error` übergaben keine ID an `pulse_logging.report_error`.
3. **Activity Check:** Ein Fehler beim Senden des Tagesberichts wurde mit `print` geschluckt, der Lauf galt als
   erfolgreich und fehlte in `/healthz`. Fehler im Warnlauf pro Guild fehlten in `/healthz`.
4. **Suite-Automatik:** Ein Fehler in `sync_members` (z. B. Discord 403) übersprang `warn_sync` …
   `update_presence` und tauchte nur in `print` auf.
5. **TeamOS-Housekeeping:** Ein Fehler in `pulse_next.members()` wurde nur ausgegeben.
6. **Voice-Support Bereitschaft/Musik:** Ein gesperrtes Settings-DB beendete beide Loops (über Punkt 1).
7. **Housekeeping (pulse_pro):** Ein `db.notify`-Fehler bei überfälligen Aufgaben übersprang Ticket-Erinnerungen,
   LOA-Erinnerungen und Presence-Update dieses Laufs.
8. **KI-Moderationsworker:** Eine nicht numerische `review_message_id` in einem gespeicherten Fall
   (`ValueError` bei `int()`) beendete den Worker vor der Queue-Schleife. Die Moderation war dann bis zum Neustart aus.

## Verhalten vorher → nachher

- `pulse_jobs.tracked`: Ein Fehler wird wie bisher gezählt, geloggt, alarmiert (30 min gedrosselt) und in
  `/healthz` angezeigt. Er beendet den Loop aber **nicht** mehr. `CancelledError` (Shutdown, `cog_unload`) wird
  weiter durchgereicht. Bei Fehlern in Folge überspringt ein schneller Loop Läufe mit begrenztem
  exponentiellem Backoff (`Intervall · 2^(n-1)`, höchstens 300 s). Loops ab 5 min Intervall werden nie
  übersprungen. Ein erfolgreicher Lauf setzt den Backoff zurück. Das ist keine Endlosschleife mit
  Hochfrequenz: Wiederholt wird nur im bestehenden Takt des Schedulers.
- Fehler-IDs: `last_error_id` (6 Hex-Zeichen) im Job-Eintrag, im Log, in `pulse_logging.recent()` und in der
  Konsolenzeile. Bei wiederholt gleichem Fehler wird der volle Traceback höchstens alle 10 min ausgegeben,
  damit der 8-s-Musikloop keine Log-Flut erzeugt.
- `snapshot()` filtert interne Schlüssel (`_retry_at`, `_tb_*`). Neu sichtbar sind `last_error_id` und
  `consecutive_failures`, beide additiv.
- Activity Check: Ein Send-Fehler wird erneut ausgelöst, `@tracked` erfasst ihn, und der vorhandene
  5-min-Nachhol-Loop versucht es bis 20:00 erneut (Erstellung ist pro Datum idempotent). Warnlauf-Fehler
  pro Guild werden mit `record_error` erfasst, andere Guilds laufen weiter.
- Suite-Automatik: Jeder Schritt hat sein eigenes `try`, ein Fehler wird erfasst, die übrigen Schritte laufen.
- TeamOS-Housekeeping: Ein `members()`-Fehler wird erfasst.
- Housekeeping: Ein `db.notify`-Fehler wird erfasst und beendet nur die betroffene Erinnerungsschleife
  (`break`, ein Eintrag statt bis zu 1000 bei gesperrter DB). LOA und Presence laufen weiter.
- KI-Moderation: Ein defekter Fall wird beim Wiederherstellen übersprungen und gemeldet, der Worker startet.
- `tools/jobs_perms.py` erkennt `@tracked`/`@pulse_jobs.tracked` und beschreibt diese Loops korrekt.
  `docs/JOBS_UND_RECHTE.md` ist neu generiert (Zeilennummern waren veraltet).

## Veränderte Dateien

- `pulse_jobs.py` – Loop überlebt Fehler, Backoff, Fehler-IDs, gedrosselte Tracebacks
- `cogs/activity_check.py`, `cogs/pulse_suite.py`, `cogs/pulse_next.py`, `cogs/pulse_pro.py`,
  `cogs/ai_moderation.py` – siehe oben
- `tools/jobs_perms.py`, `docs/JOBS_UND_RECHTE.md` – Inventar
- `tests/test_phase14_6_job_resilience.py` – 13 neue Regressionstests
- `tests/test_stability.py`, `tests/test_job_coverage.py` – an das neue Verhalten angepasst
- `docs/PHASE14_6_BERICHT.md`

Keine Änderungen an Discord-IDs, URLs, Slash-Commands, `custom_id`, DB- oder JSON-Schema.

## Tests (lokal, Windows, Python 3.14.6)

| Prüfung | Ergebnis |
|---|---|
| `tests/test_phase14_6_job_resilience.py` vor dem Fix | 12 von 13 rot (Fehlverhalten reproduziert) |
| dito nach dem Fix | 13/13 OK |
| `python -m ruff check .` | All checks passed |
| `python -m compileall -q .` | OK |
| `tests/feature_smoke.py`, `tests/smoke_test.py` | OK, OK |
| `python -m unittest discover -s tests -p 'test_*.py'` | 1307 Tests OK (18 skipped) |
| `python -m unittest tests.test_e2e_dashboard` (Playwright) | 16 Tests OK |
| CI-Leckprüfung (`*.json`/`*.db`/`logs/`) | keine neuen Dateien durch Tests |
| Docker-Compose-Integration, Python 3.15 | **nicht lokal ausgeführt** (kein Docker) – läuft in der CI |

Angepasste Bestandstests (bewusste Verhaltensänderung, kein Funktionsverlust):

- `tests/test_stability.py`: `@tracked` liefert nach einem Fehler `None`, statt den Fehler erneut auszulösen.
  Zählung und `last_error` werden weiter geprüft.
- `tests/test_job_coverage.py`: „Eigene Endlosschleifen“ zeigt jetzt (4), weil der seit Längerem vorhandene
  `recovery._run_all_sync` beim Neugenerieren erfasst wird. Die eigentliche Prüfung, dass das verdrängte
  `pulse_features`-Housekeeping nicht gelistet ist, bleibt bestehen.

Unabhängiges Review des gesamten Diffs: Es fand genau diese zwei Testbrüche, die jetzt behoben sind.
Doppelzählung, `health_summary`, Backoff-Arithmetik, Cancellation, die Aufrufer des neuen `raise` im
Activity Check (nur die beiden `@tracked`-Loops) und die `break`-Semantik in pulse_pro hat es ohne Befund geprüft.
Hinweise aus dem Review: Netzwerkfehler in `@tracked`-Loops warten jetzt auf den nächsten Takt statt auf den
Reconnect-Backoff von discord.ext.tasks. Bei gesperrter DB kann der Fehlerzähler der Suite-Automatik bis zu
9 pro Lauf steigen, Alarme bleiben auf 30 min gedrosselt.

## Nicht behobene Risiken

- Ein Loop, der *ohne* Ausnahme hängt (z. B. ein Discord-Aufruf ohne Timeout), wird nicht erkannt. Es gibt
  weiterhin keinen Liveness-Check über `Loop.is_running()`. Der vorhandene Stall-Watchdog misst nur die
  Event-Loop-Verzögerung.
- Fehlerhistorie und Backoff-Zustand liegen nur im Speicher und gehen bei einem Neustart verloren (wie bisher).
- `auto_backup_loop` meldet einen Fehler über `job()` und über `_system_alert("backup:auto")`. Das sind zwei
  Alarme mit unterschiedlichen Schlüsseln. Beide sind gedrosselt und bleiben bewusst unverändert, weil der
  Backup-Alarm eigenständig ist.
- Der Activity-Check-Nachhol-Loop meldet Läufe außerhalb von 06–20 Uhr als erfolgreich (No-op). Das ist
  korrekt, aber kein Beleg für einen erstellten Check.
- Docker-Compose-Integration und Python-3.15-Job konnten lokal nicht laufen (kein Docker). Sie laufen in der CI.

## Verworfene Verdachtsfälle

- **03:00-Purge im Housekeeping hängt von der Loop-Phase ab:** Ein 5-min-Loop trifft jedes 5-min-Fenster
  genau einmal. Ausfallen kann er nur bei einem Lauf, der länger als ein Intervall dauert. Kein Fix.
- **`automation_loop` ohne Backoff:** Fester 60-s-Abstand plus 60 s Pause nach einem Fehler, Alarm gedrosselt,
  `critical=True`. Das ist keine Hot-Loop. Kein Fix.
- **KI-Moderationsqueue unbegrenzt:** Sie ist mit `maxsize` begrenzt, `QueueFull` wird gezählt und als
  „nicht geprüft“ vermerkt.
- **Doppelte Scheduler:** Alle Loops starten in `cog_load` mit `start()`. `tasks.Loop.start()` löst bei
  laufendem Loop `RuntimeError` aus, `cog_unload` ruft `cancel()` auf. Spawns behalten ihre Handles
  (Phase 14.5b). Kein Befund.
- **Teamliste/Tickets/Verifikation/Abmeldungen:** Sie fangen breit ab und rufen `record_error` auf. Mit dem
  neuen Wrapper überleben sie zusätzlich unerwartete Fehler außerhalb dieser Blöcke.
- **`pulse_tasks.spawn`-Kapazität:** Kernaufgaben sind `critical=True`. Verhalten durch Phase-14.5b-Tests belegt.

## RAM und Performance

Keine neuen Threads, Queues, Tasks oder Abhängigkeiten. Pro Job kommen höchstens fünf kleine Felder im
vorhandenen Registry-Dict hinzu (13 + 4 Jobs). Der Backoff *senkt* die Last bei Dauerfehlern: Der Musikloop
läuft dann höchstens alle 300 s statt alle 8 s. Die Traceback-Drosselung senkt das Log-Volumen.
