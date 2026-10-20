# Phase 0 – Bestandsaufnahme (Bericht)

Branch `verbesserung/phase-0-bestandsaufnahme`, Basis `main` @ `8d66bdf`, Stand 20.10.2026. Geändert wurden nur `docs/` und `tools/`. **Nichts gepusht.**

## P0-1 Baseline

| Prüfung | Umgebung | Ergebnis |
|---|---|---|
| Version | `pulse_version.py` | **7.9.7** |
| venv | frisch, Python **3.14.6**, nur `requirements.txt` (discord.py 2.7.1, FastAPI 0.153.0) | angelegt |
| `python -m compileall -q .` | venv | ✅ exit 0 |
| `tests/feature_smoke.py` | venv **ohne** `tzdata` | ❌ `ZoneInfoNotFoundError: Europe/Berlin` |
| `tests/test_pulse_intelligence.py` | venv | ✅ 6 Tests OK |
| `tests/smoke_test.py` | venv | ✅ OK |
| `unittest discover` | venv **ohne** `tzdata` | ❌ 270 Tests, 4 Fehler (alle Zeitzone: `test_pulse_activity_utils` lädt nicht, 3× `test_voice_support_utils`) |
| alle vier Gates + `unittest discover` | venv **mit** `pip install tzdata` | ✅ alle grün, **275 Tests OK** |

**Folgerung:** K-13 ist belegt – ohne `tzdata` laden `activity_check` und die Voice-Öffnungszeiten auf Systemen ohne IANA-Daten (Windows, schlanke Container) nicht. Der Linux-Host von Bot-Hosting bringt die Daten mit, deshalb fiel es live nicht auf.

**Doku-Abweichungen**
- `CLAUDE.md` nennt als letzten geprüften Commit `b1019dc…`; tatsächlich `8d66bdf` (über 30 Commits später).
- `CHANGELOG.md` beginnt mit zwei unversionierten Abschnitten; letzte Versionsüberschrift ist **7.8.27**, obwohl der Code 7.9.7 meldet.
- `README.md` beschreibt „Pulse Intelligence 7.9.0“.
- 15 `__pycache__/*.pyc` sind getrackt (O-05).
- `requirements.txt`: `discord.py>=2.3`, der Code braucht Components V2 (≥ 2.6) – lokal und live läuft 2.7.1.
- Dateigrößen: `webserver.py` 9.905, `pulse_pro.py` 2.688, `pulse_ultimate.py` 2.325 Zeilen.

## P0-2 Routen → `docs/ROUTEN.md`, `docs/routen_snapshot.json`, `tools/route_map.py`
- **266** effektive Routen (Methode × Pfad), ermittelt am laufenden App-Objekt inkl. der Operations-Suite-Routen, die erst beim Cog-Start registriert werden.
- **1 Doppelung bestätigt:** `GET /ultimate` → aktiv `webserver.py:3344`; `pulse_ultimate.py:751` wird nie erreicht.
- **21 verdrängte Handler** (toter Code) mit aktivem Ersatz aufgelistet.
- **Neu gefunden (nicht im Plan):** FastAPIs automatische API-Dokumentation ist **öffentlich**: `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` zeigen allen ohne Login alle Endpunkte mit Parametern. Vorschlag **K-17**: `docs_url=None, redoc_url=None, openapi_url=None` (oder nur für Admins).
- Übrige Routen ohne Login sind bewusst öffentlich (Login/OAuth/Health/Manifest/robots/Service Worker/öffentlicher Status – letzterer gibt nur Bot-/DB-Zustand aus). `/ultimate/api/v2/*` ist per API-Schlüssel geschützt.

## P0-3 Daten → `docs/DATENLANDKARTE.md`, `tools/data_map.py`
- 24 Ablagen, 81 Tabellen. **59 Tabellen fehlen im JSON-Backup** (`export_state` exportiert 22).
- **In keinem Backup:** `pulse_suite.db`, `teamliste.db`, `buero_system.db`, `rp_verwaltung.db`, `flaggenquiz.db`, Social-/KI-Moderations-/Namenspräfix-/Ankündigungsarchiv-JSON, `uploads/`, `.session_secret`.
- Transkripte > 2 MB bzw. > 10 MB gesamt werden im JSON-Backup still übersprungen.
- Unbegrenztes Wachstum bestätigt: `shifts.json` (History), `logs.json`, `applications.json`.

## P0-4 Jobs & Rechte → `docs/JOBS_UND_RECHTE.md`, `tools/jobs_perms.py`
- **13 `@tasks.loop`**, **4 eigene Dauerschleifen** (Ultimate-Automation, Auto-Backup, v4-Housekeeping in `pulse_features.py`, KI-Moderations-Worker), **11 `create_task`** ohne zentrale Referenzhaltung.
- Mehrere Loops ohne Fehlerbehandlung (`activity_check` Recovery/06:00, `voice_support.duty_status_loop`, `teamliste`, `pulse_next.housekeeping`) bzw. mit stillen `except … pass` (`pulse_pro.housekeeping_loop`, `voice_support.music_loop`, `pulse_features.housekeeping`).
- **20 Rechte-Schlüssel** auf vier Ebenen (PERM_KEYS, EXTRA_PERMS/Dashboard-Profile, `next_permission_overrides`, `command_roles`).
- **A-03 [Prüfen] → bestätigt:** `webserver.compute_perms` wertet `config.permissions` nur für Teamrollen aus, `pulse_pro.patched_compute` liest die `EXTRA_PERMS` dagegen für **jede** Rolle des Mitglieds. Ein verwaister Rechte-Eintrag einer Nicht-Teamrolle vergibt damit Rechte (Verstoß gegen deny-by-default).

## Noch nicht verifiziert (folgt in der jeweiligen Phase)
K-04 Voice-Blob-Race, P-14 Speicherkosten, O-02 Proxy-IP beim Bewerbungslimit – laut Plan erst beim jeweiligen Punkt belegen.
