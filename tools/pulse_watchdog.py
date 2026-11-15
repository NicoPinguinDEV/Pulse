#!/usr/bin/env python3
"""Audit #154: unabhängiger Down-Detector für Pulse (läuft außerhalb des Bot-Prozesses, nur Standardbibliothek).

Ein systemd-Timer (``systemd/pulse-watchdog.timer``) ruft das Skript regelmäßig auf. Es fragt ``/healthz`` ab:
Bei ``FAILURES`` aufeinanderfolgenden Fehlern wird einmal ein „Ausfall“ in einen privaten Betriebskanal gemeldet
(Discord-Webhook), bei der Rückkehr einmal „wieder erreichbar“. Dazwischen schweigt es (kein Dauer-Spam).

Umgebung (nie im Repository ablegen):
  PULSE_WATCHDOG_WEBHOOK  Discord-Webhook-URL des Betriebskanals (Pflicht, sonst nur Ausgabe/Exit-Code)
  PULSE_WATCHDOG_URL      Standard http://127.0.0.1:25095/healthz
  PULSE_WATCHDOG_STATE    Zustandsdatei, Standard /var/lib/pulse-watchdog/state.json
  PULSE_WATCHDOG_FAILURES Fehler bis zur Meldung, Standard 3
Exit-Code: 0 gesund, 1 ungesund (auch wenn bereits gemeldet), 2 Konfigurationsfehler.
Das Skript startet, stoppt oder ändert nichts an Pulse oder Discord-Rollen.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:25095/healthz"
DEFAULT_STATE = "/var/lib/pulse-watchdog/state.json"
TIMEOUT = 10
USER_AGENT = "pulse-watchdog/1"


def _say(text: str) -> None:
    """Ausgabe ins Journal; ein Terminal ohne UTF-8 (cp1252, C-Locale) darf den Lauf nie abbrechen."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def check(url: str, opener=urllib.request.urlopen, timeout: int = TIMEOUT) -> tuple[bool, str]:
    """``(gesund, Detail)``. Jede Antwort außer HTTP 200 oder jeder Netzwerkfehler gilt als ungesund."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return (response.status == 200, f"HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, type(exc).__name__


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def decide(state: dict, healthy: bool, detail: str, failures_needed: int, now: float) -> tuple[dict, str | None]:
    """Reine Zustandslogik: ``(neuer Zustand, Meldung oder None)``. Meldet Ausfall und Rückkehr je genau einmal."""
    new = dict(state)
    if healthy:
        was_down = bool(state.get("reported_down"))
        down_since = float(state.get("down_since") or 0)
        new.update(failures=0, reported_down=False, down_since=0)
        if was_down:
            minutes = max(1, int((now - down_since) // 60)) if down_since else 0
            return new, "🟢 **Pulse ist wieder erreichbar.**" + (f" Ausfall ca. {minutes} Min." if minutes else "")
        return new, None
    failures = int(state.get("failures") or 0) + 1
    new["failures"] = failures
    if not state.get("down_since"):
        new["down_since"] = now
    if failures >= failures_needed and not state.get("reported_down"):
        new["reported_down"] = True
        return new, f"🔴 **Pulse ist nicht erreichbar** ({detail}, {failures} Prüfungen in Folge). Bitte Container und Logs prüfen."
    return new, None


def notify(webhook: str, message: str, opener=urllib.request.urlopen) -> bool:
    """Eine Nachricht an den Betriebskanal. Nur https-Discord-Webhooks, keine Erwähnungen. Fehler brechen nichts ab."""
    if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith("https://discordapp.com/api/webhooks/"):
        return False
    body = json.dumps({"content": message[:1900], "allowed_mentions": {"parse": []}}).encode("utf-8")
    request = urllib.request.Request(webhook, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def run(env=os.environ, opener=urllib.request.urlopen, now=time.time) -> int:
    url = env.get("PULSE_WATCHDOG_URL", DEFAULT_URL)
    if not url.startswith(("http://", "https://")):
        print("Ungültige PULSE_WATCHDOG_URL", file=sys.stderr)
        return 2
    try:
        needed = max(1, min(20, int(env.get("PULSE_WATCHDOG_FAILURES", "3"))))
    except ValueError:
        print("PULSE_WATCHDOG_FAILURES muss eine Zahl sein", file=sys.stderr)
        return 2
    state_path = Path(env.get("PULSE_WATCHDOG_STATE", DEFAULT_STATE))
    healthy, detail = check(url, opener)
    state, message = decide(load_state(state_path), healthy, detail, needed, now())
    sent = True
    if message:
        webhook = env.get("PULSE_WATCHDOG_WEBHOOK", "").strip()
        _say(message)
        sent = notify(webhook, message, opener) if webhook else False
        if not sent:
            # Nicht als erledigt speichern: beim nächsten Lauf wird erneut gemeldet, statt still zu bleiben.
            if healthy:
                state.update(reported_down=True, down_since=load_state(state_path).get("down_since", 0))
            else:
                state["reported_down"] = False
    try:
        save_state(state_path, state)
    except OSError as exc:
        print(f"Zustand nicht speicherbar: {type(exc).__name__}", file=sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(run())
