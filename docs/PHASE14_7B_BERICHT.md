# Phase 14.7b – Crash-sicheres Speichern und Recovery der Schichthistorie

Branch `phase/14-7b-crash-safe-shift-history`, Ausgangs-Commit `8b74844` (Merge von Phase 14.7).
Keine Produktionsmigration, keine Änderung an VPS-Daten oder an `PULSE_SHIFT_HISTORY_BACKEND` auf dem VPS.
Lokale Commits, kein Push, kein Merge, kein Deployment.

## 1. Ursache

`pulse_shift_history.save()` (Phase 14.7) lief so ab:

1. `BEGIN IMMEDIATE`, geänderte Zeilen in `shift_history` schreiben.
2. `shifts.json` ersetzen (aktive Schichten und neuer Marker mit `rows = n+1`).
3. `COMMIT`.

Stirbt der Prozess zwischen 2 und 3, verwirft SQLite die Zeilen. Die Datei enthält dann schon den neuen Stand: Die Schicht ist nicht mehr aktiv, und der Marker erwartet `n+1` Einträge. Die Datenbank hat aber nur `n`.
Beim nächsten Lesen meldet `attach()` den Fehler „pulse.db enthält n statt n+1“, und das Schreiben bleibt gesperrt. Der Schichteintrag ist verloren, eine automatische Wiederherstellung gab es nicht.
Vor der Änderung mit einem echten Prozessabbruch nachgewiesen: Nach einem Absturz direkt nach `os.replace` ergibt der Neustart `load_json → ShiftHistoryUnavailable`, und `shifts.json` wird als „beschädigt“ gesperrt.

Ein bloßes Vertauschen von `COMMIT` und `os.replace` reicht nicht. Nach einem Absturz zwischen `COMMIT` und dem Ersetzen der Datei stünde der neue Eintrag in der Historie, die Schicht wäre in `shifts.json` aber noch aktiv. Die Schicht ließe sich dann ein zweites Mal beenden, und der Eintrag stünde doppelt in der Historie.

## 2. Neue Speicherarchitektur

Geprüfte Varianten:

| Variante | Bewertung |
|---|---|
| Reihenfolge tauschen | reicht nicht (siehe oben) |
| Aktive Schichten ebenfalls nach SQLite | löst das Problem, ändert aber das Format von `shifts.json`, alte Backups, Rückweg und alle Leser – zu groß und nicht rückwärtskompatibel |
| Eigene Journaldatei neben `shifts.json` | eine dritte Datei mit eigenem Absturzfenster, die auch ins Backup muss |
| **Journal in derselben SQLite-Transaktion (gewählt)** | ein einziger atomarer Commit-Punkt, keine neue Datei, keine neue Abhängigkeit, Backup schon abgedeckt |

Ablauf eines Speichervorgangs (`save`, weiterhin unter `webserver._io_lock`):

1. `BEGIN IMMEDIATE`. Das Paar aus Marker in `shifts.json` und `shift_history_meta` muss in einem bekannten Zustand sein (siehe 3.).
2. In derselben Transaktion: nur die geänderten Zeilen, `revision` (+1) und `journal` als kleines JSON mit `revision`, `payload`, `payload_sha256` und `prev_sha256`. `payload` ist der vollständige neue Inhalt von `shifts.json` (aktive Schichten, weitere Schlüssel, Marker; ohne Historie). `prev_sha256` ist die Prüfsumme der Datei, die ersetzt wird. Danach `COMMIT`: der **einzige Commit-Punkt**.
3. Danach wird `shifts.json` wie bisher geschrieben (tmp-Datei, fsync, `os.replace`), und zwar genau mit dem Inhalt aus dem Journal. Der Marker trägt jetzt `revision`.

Unverändert bleiben: JSON-Schlüssel, `load_shifts()`, `load_shifts_raw()`, `save_json()` als einzige Schreibstelle, Tabellen und Spalten von `shift_history`, `/dienst`, URLs, IDs und `custom_id`s.
Neu sind nur zwei Zeilen in `shift_history_meta` (`revision`, `journal`) und das Markerfeld `revision`. Ältere Marker ohne `revision` (Phase 14.7) zählen als Revision 0.
Das Journal ist so groß wie die aktiven Schichten (wenige KB) und wird bei jedem Speichern überschrieben. Es wächst also nicht.

## 3. Recovery-Ablauf

`pulse_shift_history.recover(ws)` läuft beim Start in `webserver.init_runtime`, und zwar vor `apply_configured_backend`. Dieselbe Prüfung macht `attach()` bei jedem Lesen über `load_json`. Ein Speichern stellt zuerst einen offenen Rest fertig. Alles läuft unter `_io_lock`.

| Zustand (Datei ↔ pulse.db) | Ergebnis |
|---|---|
| JSON-Modus (kein Marker) | `json`, nichts zu tun |
| Revisionen gleich (Phase-14.7-Marker ohne `revision` gilt als 0) | `consistent` |
| pulse.db genau **eine** Revision voraus, Journal intakt (Prüfsumme, Revision, Marker), Datei entspricht `prev_sha256` | `recovered`: `shifts.json` wird aus dem Journal geschrieben, erneut gelesen und verglichen; Logzeile ✅, Eintrag im Systemstatus |
| Journal beschädigt oder unvollständig | `blocked`: „Änderungsjournal … beschädigt“ |
| pulse.db eine Revision voraus, Datei seither verändert | `blocked`: „Schreibvorgang unvollständig …, nicht automatisch korrigiert“ |
| pulse.db mehr als eine Revision voraus **oder** älter als die Datei | `blocked`: „Revision von shifts.json (x) passt nicht zu pulse.db (y)“ |
| andere Generation (Datei aus einer anderen Migration) | `blocked`: „unterschiedlichen Ständen“ (wie in 14.7) |

`blocked` bedeutet: `recover()` wirft keine Ausnahme, der Start von Pulse läuft weiter. `shifts.json` wird über `_CORRUPT_JSON` gegen Schreiben gesperrt. Der Grund steht im Log (🔴), im Systemstatus (Zeile „Datendatei“ und neue Zeile „Schichthistorie“) und in `scripts/shift_history.py status` (`consistent: false`).
Nichts wird still korrigiert. Wiederhergestellt wird nur ein bereits committeter Stand, den die Datenbank als Wahrheit festhält. Eine zweite Recovery findet nichts mehr zu tun (idempotent).
`rollback()` (Rückweg zu JSON) stellt zuerst einen offenen Rest fertig und setzt nie zwei Hälften zusammen.

**Fehler beim Schreiben der Datei nach dem `COMMIT`** (z. B. volle Festplatte, unter Windows eine vom Virenscanner gehaltene Datei): Der Speichervorgang **ist erfolgt**, denn `pulse.db` hält ihn. `save_json` meldet deshalb keinen Fehler an den Aufrufer. Sonst sähe ein Teammitglied „fehlgeschlagen“, obwohl die Schicht beendet ist, und Folgeaktionen wie Audit oder Log blieben aus.
Sichtbar wird der Fall trotzdem: im Log (🔴 „Revision n in pulse.db gespeichert, shifts.json nicht nachgezogen“), im Systemstatus (Zeile „Schichthistorie“ rot) und in `status()` als `pending`. Der nächste Lesezugriff zieht die Datei nach. Scheitert auch das, wird gesperrt.
Eine solche Sperre hebt der nächste erfolgreiche Versuch wieder auf, ein Neustart ist dafür nicht nötig. Voraussetzung ist ein erneut geprüftes Paar aus intaktem Journal und unveränderter Datei. Ein zweites Beenden derselben Schicht ist nicht möglich (Test).

Weitere Absicherungen:
* Zeigt der Modus-Cache veraltet „SQLite“, ist die Datei aber im JSON-Modus, speichert `save()` sie als JSON und schaltet nie unbemerkt um.
* Das Journal wird nur angenommen, wenn seine Generation zu `pulse.db` passt und es keine Historie enthält.

## 4. Absturztests

`tests/test_shift_history_crash.py` mit dem Kindprozess `tests/shift_crash_child.py`. Jeder Absturztest startet einen echten Pulse-Prozess (`import webserver`, temporäre Pfade). Dieser beendet eine Schicht und stirbt an der gewählten Stelle hart mit `os._exit`, also ohne `finally`, Flush oder Rollback. Danach startet ein **frischer Prozess**, der `recover()` zweimal ausführt, alles neu liest, eine weitere Schicht beendet und das Ergebnis meldet.

| Test | Absturzpunkt | Erwartung nach Neustart |
|---|---|---|
| `test_crash_after_begin` | nach `BEGIN IMMEDIATE` | alter Stand, `consistent` |
| `test_crash_after_rows_changed` | nach der ersten Zeilenänderung | alter Stand |
| `test_crash_right_before_commit` | unmittelbar vor `COMMIT` | alter Stand |
| `test_crash_after_commit` | unmittelbar nach `COMMIT` | neuer Stand, `recovered` |
| `test_crash_while_writing_the_json_file` | tmp-Datei geschrieben, vor `os.replace` | neuer Stand, `recovered` |
| `test_crash_right_after_the_json_file_was_replaced` | nach `os.replace` | neuer Stand, `consistent` |
| `test_crash_before_the_mode_cache_is_updated` | vor `remember_mode` (interner Cache) | neuer Stand |
| `test_every_point_is_covered` | alle Punkte des Kindprozesses haben einen Test | – |
| `test_edit_trim_and_save_without_history…` | Bearbeiten (UPDATE), Kürzen (DELETE), Speichern ohne `history`; jeweils nach der Zeilenänderung, vor und nach `COMMIT` (8 Fälle) | alter bzw. neuer Stand |
| `test_crash_while_the_recovery_itself_writes` | der Neustart stirbt selbst beim Nachziehen (vor/nach `os.replace`) | dritter Start: neuer Stand |
| `ShutdownTests…random_moments` | 4× `kill`/`terminate` (SIGKILL/SIGTERM, unter Windows TerminateProcess) zu zufälligen Zeiten während einer Schleife von Speichervorgängen | genau ein Präfix der Schleife ist erledigt, keine Schicht fehlt oder ist doppelt |

Weitere Tests im selben Modul (im Prozess):

* Recovery-Regeln: beschädigtes Journal, falsche Prüfsumme, Datei nach dem Absturz verändert, pulse.db älter als die Datei, pulse.db mehrere Revisionen voraus. In jedem Fall: `blocked`, Lesen liefert den Standardwert, Schreiben ist gesperrt, die Datei bleibt byteweise unverändert, Systemstatus und `status()` melden den Fehler.
* Recovery wird geloggt und im Systemstatus angezeigt. Der erste Lesezugriff im laufenden Prozess stellt ebenfalls wieder her.
* Dateischreibfehler nach `COMMIT`: kein Fehler für den Aufrufer, Log und Systemstatus melden ihn, kein Doppeleintrag, zweites Beenden wird abgelehnt.
* Nachziehen scheitert zweimal (PermissionError) und klappt beim dritten Lesen: Die Sperre löst sich ohne Neustart.
* Veralteter Modus-Cache schaltet eine JSON-Datei nicht um.
* Journal einer fremden Generation oder mit untergeschobener Historie (mit gültiger Prüfsumme) wird gesperrt.
* Sperr-, Status- und Systemstatus-Texte enthalten keine Mitglieds-IDs, Notizen oder Pfade.
* Zeilenenden nach dem Absturz umgestellt (CRLF ↔ LF, etwa beim Kopieren Windows ↔ Linux): wird erkannt und wiederhergestellt.
* Rückweg nach einem Absturz: kein Doppeleintrag.
* `init_runtime`: `recover` läuft vor der Umschaltung. `recover` wirft nie eine Ausnahme. JSON-Modus braucht keine Recovery.
* Phase-14.7-Marker ohne `revision`: läuft weiter, das erste Speichern setzt Revision 1.
* Parallel: 4 schreibende Threads (je Laden/Ändern/Speichern unter `_io_lock`, wie `apply_shift_action` ohne `await`) und 2 lesende Threads. Kein Zwischenstand zeigt eine Schicht doppelt oder gar nicht.
* Backup: Backup eines abgestürzten Stands → Recovery → Weiterschreiben → Restore genau dieses Backups → erneute Recovery ergibt denselben richtigen Stand. Backup unmittelbar nach einem Speichern ist konsistent. Altes vollständiges JSON-Backup stellt den JSON-Modus her. `shifts.json` einer anderen Generation wird gesperrt. Die SHA-256 jedes verwendeten Backup-ZIPs ist vorher und nachher gleich: Backups werden weder verändert noch gelöscht.

Geprüft wird nach jedem Neustart: Anzahl und Reihenfolge der Einträge, **alle Payload-Felder** (direkter Vergleich der Listen, einschließlich Unicode-Feld), aktive Schichten, Generation, Marker-Revision gleich DB-Revision, `marker_rows` gleich `db_rows`, abgeleitete Teamstunden je Mitglied, eindeutige `shift_event_id`s und dass danach normal weitergeschrieben werden kann.

Vor der Änderung schlugen 7 von 8 Prozessabsturztests fehl. Formal lag das an der noch fehlenden `recover()`-API; der eigentliche Datenfehler wurde deshalb zusätzlich direkt mit dem alten Code nachgewiesen (siehe 1.).

Angepasst wurde ein bestehender Test: `test_failed_commit_restores_the_previous_json` hieß jetzt `test_failed_file_write_after_commit_keeps_the_committed_state`. Er beschrieb die alte Reihenfolge (Datei vor `COMMIT`, danach Datei zurücksetzen). Jetzt weist er das neue Verhalten nach: Der Stand ist committet, `shifts.json` wird nachgezogen und ist konsistent.
`test_failing_commit_rolls_back_rows_and_file` gilt unverändert: Scheitert der `COMMIT`, bleibt die Datei byteweise gleich.

## 5. Datenintegritätsnachweise

* Absturz an jedem Punkt: Es gibt nur zwei Ergebnisse, alter oder neuer Stand, beide vollständig. Eine Schicht ist nie gleichzeitig aktiv und in der Historie und nie in keinem von beiden.
* Wiederholte Recovery ist idempotent (`second == consistent`, Datei bleibt gleich).
* Ein unklarer Zustand wird nie überschrieben (byteweiser Vergleich der Datei in den Sperrtests).
* Benchmark (20.000 Einträge, 200 Mitglieder): Prüfsummen der Seed-Historie, Teamstunden und Wochenwerte sind vor und nach der Änderung identisch (siehe 6.).

## 6. Performance und RAM

`tools/shift_history_bench.py --history 20000 --members 200 --backend sqlite`. „Vorher“ ist ein separater Worktree von `8b74844`, „nachher“ der Endstand dieses Branches (nach den Review-Fixes). Gemessen wurden 3 abwechselnde Läufe, angegeben ist der Median (Spanne). Python 3.14.6, Windows, Rechner nicht exklusiv.

| Messwert | vorher | nachher |
|---|---|---|
| Laden (`load_shifts`) | 44,0 ms (39,7–44,6) | 39,2 ms (35,8–39,4) |
| Wochenstatistik (200 Mitglieder, Code unverändert) | 55,9 ms (55,2–59,3) | 66,0 ms (56,1–69,0) |
| Schichtende schreiben | 131,9 ms (129,1–144,2) | 136,7 ms (134,7–160,3) |
| Spitzen-RAM beim Laden | 14,72 MiB | 14,72 MiB |
| JSON-Parses / SQLite-Verbindungen (Laden, Schreiben) | 3/1, 8/7 | 3/1, 8/7 |
| Prüfsummen (Seed, Teamstunden, Wochenwerte) | – | identisch |

Die Wochenstatistik nutzt keinen geänderten Code. Ihre Spanne zeigt das Messrauschen dieses Rechners, ebenso der Unterschied beim Laden.
Ein Speichervorgang kostet jetzt etwa 5 ms mehr (Median). Das sind zwei SHA-256 über die kleine Datei und ein Upsert von zwei Meta-Zeilen. Das Lesen ist unverändert.
Zusätzlicher RAM pro Speichervorgang: ungefähr das Doppelte der kleinen `shifts.json` (Journaltext). Keine neuen Threads, Scheduler, Queues oder Abhängigkeiten.
Die Recovery beim Start liest die Historie einmal, wie es auch das erste `load_json` tut. Sie legt **keine** Datenbankkopie an.
Die Systemstatus-Zeile parst im JSON-Modus die große Datei nicht. Sie nutzt den vorhandenen Modus-Cache und liest die Datei nur als Text, und das nur, wenn sie sich seit dem letzten Zugriff geändert hat.

## 7. Veränderte Dateien

| Datei | Änderung |
|---|---|
| `pulse_shift_history.py` | Commit-Punkt in SQLite, `revision` und `journal`, `_pair_state`, `_journal`, `_roll_forward`, `recover`, `system_check`; `attach(raw, ws, path)`; `status` mit Revisionen/`pending`/`consistent`; Migration setzt Revision 0; Rückweg stellt zuerst wieder her |
| `webserver.py` | `init_runtime` ruft `recover` vor der Umschaltung; `load_json` übergibt `ws`/Pfad an `attach`; `_mark_corrupt` nennt bei der Schichthistorie den Grund |
| `pulse_system_checks.py` | Zeile „Schichthistorie“ im Systemstatus |
| `scripts/shift_history.py` | `consistent` berücksichtigt einen offenen Schreibvorgang (weiterhin nur lesend) |
| `tests/test_shift_history_crash.py`, `tests/shift_crash_child.py` | neu: Absturz-, Recovery-, Parallel- und Backup-Tests |
| `tests/test_shift_history_sqlite.py` | ein Test an die neue Semantik angepasst (siehe 4.) |
| `docs/VERBESSERUNGSPLAN.md` | D-07 und A-05 ergänzt |
| `docs/PHASE14_7B_BERICHT.md` | dieser Bericht |

## 8. Qualitätssicherung

Alle Läufe lokal auf dem Endstand (Windows 11, Python 3.14.6):

| Prüfung | Ergebnis |
|---|---|
| Regressionstests zuerst | 7 von 8 Prozessabsturztests schlugen vor der Änderung fehl, der Datenfehler wurde direkt nachgewiesen (siehe 1. und 4.) |
| `tests.test_shift_history_crash` + `tests.test_shift_history_sqlite` | `Ran 68 tests – OK` (nach dem letzten Fix zusätzlich `RecoveryRuleTests`: 18 OK) |
| Ruff (`python -m ruff check .`) | All checks passed |
| `python -m compileall -q .` | OK |
| `tests/feature_smoke.py`, `tests/smoke_test.py` | beide OK |
| unittest discovery (`-s tests -p 'test_*.py'`) | `Ran 1379 tests – OK (skipped=18)` (vor 14.7b: 1345) |
| Playwright-E2E (`tests.test_e2e_dashboard`) | `Ran 16 tests – OK` |
| CI-Prüfung „von Tests angelegte Dateien“ | keine (nur vorhandene, nicht versionierte `.claude/`-Dateien des Nutzers) |
| Performance vorher/nachher | siehe 6. |
| Unabhängiges Review (separater Agent, nur lesend) | kein Weg zu Verlust, Duplikat oder vermischten Revisionen gefunden; 2 mittlere und 4 niedrige Befunde, alle umgesetzt (Tabelle unten) |
| **Python 3.15** | **lokal nicht ausgeführt**, nur 3.14 installiert. Läuft im CI-Job mit der Version aus dem `Dockerfile`, sobald der Branch gepusht wird |
| **Docker-Compose-Integration** | **lokal nicht ausgeführt**, kein Docker in dieser Umgebung. Läuft im CI-Job „container“ nach einem Push |

Review-Befunde und Umsetzung:

| Befund | Schwere | Umsetzung |
|---|---|---|
| Ein gescheitertes Nachziehen sperrt bis zum Neustart | mittel | `_roll_forward` hebt die veraltete Sperre nach erneuter Prüfung auf; Test „zweimal Fehler, dann Erfolg“ |
| Aufrufer sieht einen Fehler, obwohl gespeichert wurde | mittel/niedrig | nach `COMMIT` kein Fehler an den Aufrufer, Meldung über Log, Systemstatus und `pending` |
| `save()` prüft den Modus nicht (veralteter Cache) | niedrig | JSON-Datei wird als JSON gespeichert; Test |
| Pfade in Statusmeldungen bei fremden Ausnahmen | niedrig | nur noch Typname; Test „keine Mitgliedsdaten/Pfade“ |
| Journal härten | niedrig | Generation muss passen, Historie muss leer sein; Test |
| Rückfall auf 14.7-Code | niedrig | dokumentiert (siehe 9.) |
| Testlücken (UPDATE/DELETE, ohne `history`, Absturz beim Nachziehen, CRLF, Datenschutztexte) | – | ergänzt (siehe 4.) |

## 9. Offene Risiken

* **Datenträger lügt bei fsync:** Bestätigt das Speichermedium einen fsync, ohne die Daten wirklich zu schreiben, kann keine Software-Lösung helfen. Die Revisionsprüfung erkennt das danach aber und sperrt.
* **Zwei Pulse-Prozesse auf denselben Daten** (z. B. Bot und ein zweiter Container) sind weiterhin nicht vorgesehen: `_io_lock` gilt nur innerhalb eines Prozesses. `BEGIN IMMEDIATE` verhindert gleichzeitige Datenbank-Schreibvorgänge, aber nicht Laden-Ändern-Speichern-Konflikte zwischen Prozessen (wie im JSON-Modus). `scripts/shift_history.py` schreibt nicht.
* **Laden-Ändern-Speichern ohne gemeinsames Lock** in den Aufrufern (`apply_shift_action`, `process_overdue_shifts`) ist unverändert. Sie laufen synchron im Event-Loop und ohne `await` dazwischen. Das ist kein neues Risiko und betrifft den JSON-Modus genauso.
* **Ältere Pulse-Version (14.7) nach 14.7b-Speichervorgängen:** 14.7 ignoriert `revision`/`journal` und übernimmt den Marker samt `revision` unverändert. Schreibt 14.7 danach, bleiben Datei- und DB-Revision gleich. 14.7b arbeitet anschließend normal weiter, nur das Absturzfenster von 14.7 besteht in dieser Zeit wieder. Vor einem Code-Rollback gilt weiter: zuerst den Rückweg ausführen.
* **Absturz in Phase 14.7 (alte Version) vor diesem Update:** Ein dabei verlorener Eintrag lässt sich nicht rekonstruieren. Der Zustand bleibt wie bisher gesperrt und gemeldet.
* **Nur `shifts.json` aus einem Backup zurückgespielt**, genau eine Revision älter als `pulse.db`: Das entspricht technisch dem Absturzfall und wird vorwärts wiederhergestellt (geloggt). Bei größerem Abstand oder anderer Generation wird gesperrt.
* **Fehler beim Schreiben der Datei nach `COMMIT`:** Der Aufrufer erhält „OK“, weil der Stand gespeichert ist. Bis zum Nachziehen ist das nur im Log und im Systemstatus sichtbar.
* **Wer `pulse.db` schreiben kann**, kann ein Journal fälschen und damit `shifts.json` bestimmen. Das ist dieselbe Vertrauensstufe wie Schreibzugriff auf das Datenverzeichnis. Über HTTP ist nichts Neues erreichbar.
* **Testgrenzen:** Die zufälligen Kill-Tests treffen das Fenster zwischen `COMMIT` und `os.replace` nicht garantiert; dafür gibt es die deterministischen Tests mit `os._exit`. Der Paralleltest ist durch `_io_lock` (RLock) im Prozess absichtlich seriell und weist die Lesekonsistenz nach, keinen echten parallelen Schreibvorgang.
* **Python 3.15 und Docker Compose** sind lokal nicht verfügbar (siehe 8.).
