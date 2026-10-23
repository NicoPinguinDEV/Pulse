---
name: pulse-e2e
description: Dashboard-Workflows von Pulse im echten Browser (Playwright, headless) testen oder neue E2E-Tests schreiben – Seite öffnen, Formular ausfüllen, speichern, Ergebnis und Persistenz prüfen, Desktop und Mobil. Verwenden bei UI-/Formular-/Routenänderungen im Dashboard oder wenn ein E2E-Test fehlt.
argument-hint: [run | neuer Workflow, z. B. "Teamrollen speichern"]
---

# Pulse-E2E (Playwright)

## Umgebung
- Einmalig: `python -m pip install -r requirements-dev.txt` und `python -m playwright install chromium`.
- Server: `tools/preview_server.py` – echte FastAPI-App, Fake-Guild (`tests/fakes.py`), Fake-Login (`--role admin|team`), temporäre Daten, bindet nur 127.0.0.1, **kein Discord, kein OAuth**.
- Referenztest: `tests/test_e2e_dashboard.py` (Wochenziel speichern: Desktop, Mobil, serverseitige Bereichsprüfung). Neue Tests dort oder als `tests/test_e2e_<bereich>.py` nach gleichem Muster (Server als Subprozess auf freiem Port, `TMP` auf TemporaryDirectory, externe Requests per `context.route` blockieren).

## Ausführen
```
PULSE_E2E=1 python -m unittest tests.test_e2e_dashboard -v
```
Ohne `PULSE_E2E=1` wird bei fehlendem Playwright übersprungen (normale CI/unittest discovery). Der CI-Job `e2e` in `.github/workflows/python-syntax.yml` setzt `PULSE_E2E=1`.

## Neuen Workflow-Test schreiben
1. **Aktive Route klären:** `pulse_pro.py` überschreibt viele Seiten (z. B. `/settings` → Formular an `/settings/pro-goal`). Im gerenderten HTML prüfen, welches `form action` wirklich ausgeliefert wird – nicht im ersten Treffer in `webserver.py`.
2. **Locator** über `form[action="…"]` + `name`-Attribute oder Rolle/Beschriftung, keine CSS-Klassen (Tailwind ändert sich).
3. **Ablauf:** `goto` → Status 200 → ausfüllen → `with page.expect_navigation(): click()` → Erfolgsmeldung im `#toast[role=status]` (Fehler: `role=alert`). Die URL verliert `?msg=…` per `history.replaceState`, also nicht auf die URL prüfen.
4. **Persistenz:** Seite neu laden und gespeicherten Wert prüfen.
5. **Negativfall:** Browser-Validierung (`min/max/required`) per `evaluate` entfernen und prüfen, dass der Server selbst ablehnt/begrenzt und **nichts Ungültiges gespeichert** wird. Für Rechte: `--role team` auf Admin-Seite → 403, keine Änderung.
6. **Mobil:** `viewport 375×812, is_mobile=True` und `scrollWidth - innerWidth <= 1` (kein horizontaler Überlauf).
7. `page.on("pageerror")` sammeln und am Ende `[]` erwarten.
8. Tests unabhängig von der Reihenfolge halten (alle Tests einer Klasse teilen einen Server).

## Grenzen
Keine Tests gegen den VPS, Produktions-URLs oder echte Discord-Logins. Ergebnis ehrlich berichten (bestanden/fehlgeschlagen/übersprungen + Grund).
