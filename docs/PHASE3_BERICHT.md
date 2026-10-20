# Phasenbericht Phase 3 – Performance & Stabilität

Branch `verbesserung/phase-3-performance` (von `main` = 7.11.0), **nicht gepusht, nicht gemergt, nicht deployt**. Stand 20.10.2026.

## 1. Erledigt

| ID | Commits | Testnachweis |
|---|---|---|
| P-03 Wochenstunden | b7a2a2e | `tests/test_performance.py` – identisch zur alten Berechnung (3000 Einträge inkl. Altformate) |
| P-04 Config-Cache | b7a2a2e, b7b2560 | `tests/test_performance.py` – einmal geparst, Invalidierung beim Speichern und bei externer Änderung; gemessen 262 µs statt 641 µs (64 KB) |
| P-05 Lesen ohne Schreiben | b7a2a2e | `tests/test_performance.py` – `load_shifts` ändert die Datei nicht, `process_overdue_shifts` speichert |
| P-07 Hintergrund-Tasks | 361ed21, b7b2560 | `tests/test_stability.py` – Referenz gehalten, Fehler mit Fehler-ID |
| P-08 on_ready einmal | 361ed21, b7b2560 | `tests/test_stability.py` – Warnrollen-Abgleich nach Reconnect nicht erneut |
| P-09 Slash-Sync | 361ed21 | `tests/test_stability.py` – Sync nur bei Änderung oder erzwungen |
| P-11 Fehlerseite | 62d3744 | `tests/test_stability.py` – Fehler-ID statt Details, JSON für API |
| P-12 OAuth | 62d3744 | `tests/test_stability.py` – 429, 5xx, Timeout, Erfolg |
| P-13 Doppelte Tickets | 62d3744, b7b2560 | `tests/test_stability.py` – zwei schnelle Klicks → ein Ticket |
| P-01 Event-Loop | 7951768 | Verzögerung gemessen und im Systemstatus; 30 SQLite-Verbindungen werden geschlossen |
| P-02 Live-Abfrage | 7951768 | `tests/test_stability.py` – höchstens alle 15 s berechnet |
| P-06 Job-Registry | 7951768, b7b2560 | `tests/test_stability.py` – Läufe, Fehler, Verzögerung; Zeitplan der Activity-Check-Loops unverändert |
| P-14 Speicher messen | 7951768 | Systemstatus zeigt gemessene Werte |
| P-10 Logging (Teil) | 7951768, b7b2560 | `tests/test_stability.py` – Ringpuffer begrenzt und sichtbar |

**Gates** (venv `Q:\venv`, Python 3.14, ohne Discord):
- `python -m compileall -q .` → OK
- `python tests/feature_smoke.py` → „Pulse feature smoke test: OK“
- `python tests/test_pulse_intelligence.py` → OK
- `python tests/smoke_test.py` → „Pulse smoke test: OK“
- `python -m unittest discover -s tests` → **394 Tests OK** (nach Phase 2: 377)

**Review** durch einen Subagenten mit frischem Kontext: 1 mittlerer und 6 geringe Befunde, alle in b7b2560 behoben:
- Der Config-Cache war durch `deepcopy` langsamer als Neulesen.
- Ein Index-Name kollidierte.
- `spawn` hätte ohne laufenden Loop eine offene Coroutine hinterlassen; Sicherheitsmeldungen hätten am Limit verworfen werden können.
- Die Honeypot-Karte wurde nur einmal pro Prozess geprüft.
- Bei der Fehlerzählung und im Logging gab es Doppelungen bzw. Lücken.

Laut Review ohne Befund geprüft:
- Bei P-05 ist keine Doppelverwarnung möglich (idempotent über die Schicht-ID).
- Die `closing()`-Umstellung behält Commit und Rollback bei.
- Die State-Prüfung beim OAuth-Login ist unverändert.
- Im Live-Cache landen nur nutzerunabhängige Zahlen.

**Geänderte `feature_smoke`-Asserts:** Drei Activity-Check-Asserts prüften wörtlich „Dekorator direkt über der Funktion“. Sie erlauben jetzt den Registry-Dekorator. Der Zeitplan (1 min / 5 min / 06:00) wird zusätzlich als Verhaltenstest an den echten Loop-Objekten geprüft.

## 2. Verhalten vorher → nachher

- **Dashboard schneller und ruhiger:**
  - Die Live-Zahlen oben im Dashboard werden für alle zusammen höchstens alle 15 Sekunden berechnet, nicht mehr pro offenem Tab alle 30 Sekunden.
  - Hintergrund-Tabs fragen nicht mehr ab. Bei Fehlern wird seltener gefragt, höchstens alle 5 Minuten.
  - Statistiken rechnen die Wochenstunden in einem Durchlauf statt pro Mitglied über die ganze Historie.
  - Die Config-Datei wird nur noch gelesen, wenn sie sich geändert hat.
- **Seiten schreiben nicht mehr beim Anschauen:** Vergessene Schichten erscheinen weiterhin sofort als beendet. Gespeichert und verwarnt wird aber nur noch im regelmäßigen Automatiklauf (alle 5 Minuten).
- **Nach einem Verbindungsabbruch zu Discord** werden nicht mehr bei jedem Reconnect alle Warnrollen, die Honeypot-Karte und der Security-Check abgearbeitet. Voice-Support und Server-Statistik gleichen höchstens alle 10 Minuten neu ab.
- **Slash-Befehle** werden beim Start nur noch an Discord gesendet, wenn sich etwas geändert hat. Fehlen Befehle, hilft der Knopf „Jetzt synchronisieren“ im Systemstatus.
- **Fehler:**
  - Statt „Internal Server Error“ erscheint eine Seite „Da ist etwas schiefgelaufen“ mit Fehler-ID.
  - Discord-Login-Probleme (Rate-Limit, Ausfall, Zeitüberschreitung) zeigen eine verständliche Meldung statt einer Fehlerseite.
  - Die letzten 20 Fehler stehen im Systemstatus, das Protokoll in `logs/pulse.log` (höchstens 4 MB).
- **Tickets:** Zwei schnelle Klicks erzeugen kein zweites Ticket mehr. Die Suche nach dem offenen Ticket nutzt den Datenbank-Index.
- **Systemstatus** zeigt zusätzlich:
  - jeden Hintergrundjob mit Läufen, Dauer und letztem Fehler,
  - die Event-Loop-Verzögerung (Warnung ab 250 ms),
  - den gemessenen Arbeitsspeicher und den Presence-Intent.
- **Stille Fehler:** Der Automationszyklus hat Fehler bisher kommentarlos verschluckt; jetzt werden sie protokolliert. Hintergrundaufgaben können nicht mehr unbemerkt verloren gehen.

## 3. Verworfen oder verschoben

- **P-10 vollständig:** Es gibt jetzt einen zentralen Logger, eine Logdatei und einen Fehlerpuffer. Die rund 320 `print()`-Aufrufe und die stillen `except … pass` stelle ich modulweise in Phase 4 (A-02/A-07) um. Das in einem Schritt zu tun wäre ein Massenumbau gegen die Planregeln.
- **P-06 Endlosschleifen:** Auto-Backup-Schleife und Feature-Housekeeping (eigene `while`-Schleifen) sind noch nicht in der Registry; die `tasks.loop`s und der Automationszyklus schon.
- **P-09 Server-Kopie** (`copy_global_to`) bewusst nicht eingebaut: Die Befehle würden sonst doppelt erscheinen.
- **P-14 Intents:** Es wird nur gemessen, nichts geändert. Über den Presence-Intent entscheiden wir mit echten Zahlen aus dem Betrieb.

## 4. Risiken und Schritte außerhalb des Codes

- **Erster Start nach dem Deploy:** Pulse synchronisiert die Slash-Befehle einmal, weil noch kein Hash gespeichert ist. Danach nur bei Änderungen.
  - Nach Änderungen im Developer Portal oder nach einem Datenbank-Restore bitte den Knopf im Systemstatus nutzen oder `PULSE_FORCE_COMMAND_SYNC=1` setzen.
- **Vergessene Schichten** werden bis zu 5 Minuten später gespeichert und verwarnt als bisher. Die Anzeige ist sofort korrekt.
- **`logs/`** ist ein neuer Laufzeitordner. Er ist in Git und Docker ausgeschlossen und nicht im Backup; das ist Absicht, Logs sind keine Nutzdaten.
- **Systemstatus beobachten:** Meldet „Event-Loop“ häufig über 250 ms oder „Arbeitsspeicher“ Werte nahe 180 MB, bitte Bescheid geben. Dann gehen wir gezielt an die betroffenen Stellen bzw. an den Presence-Intent.

## 5. Offene Fragen an dich

1. **Freigabe:** Darf ich Phase 3 nach `main` mergen und pushen? Vorschlag: Version 7.12.0 mit CHANGELOG-Eintrag.
2. **Nach ein paar Tagen Betrieb:** Bitte die Werte „Arbeitsspeicher“ und „Event-Loop“ aus dem Systemstatus schicken. Danach entscheiden wir über den Presence-Intent (E-5/P-14).
