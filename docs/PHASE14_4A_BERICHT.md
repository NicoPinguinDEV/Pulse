# Phase 14.4a – Legacy Health-/PWA-Routen bereinigt

**Basis:** `907802d6559a758fa321211f090675606fd85b39`  
**Umsetzung:** eigener Branch `phase/14-4a-remove-displaced-health-pwa-handlers`

## Änderungen

Aus `pulse_features.register` wurden drei nachweislich verdrängte Registrierungen entfernt:

- `GET /health` – alter Handler `health`
- `GET /manifest.webmanifest` – alter Handler `manifest`
- `GET /sw.js` – alter Handler `service_worker`

Die aktiven Handler aus `pulse_pro.register` bleiben unverändert. Der effektive Routen-Snapshot bleibt deshalb unverändert. Die ungenutzten Imports `JSONResponse` und `Response` wurden aus `pulse_features.py` entfernt.

## Absicherung

- Regressionstest prüft, dass die drei Legacy-Registrierungen nicht mehr in der statischen Routen-Inventur vorkommen.
- Derselbe Test prüft, dass alle drei aktiven Pulse-Pro-Routen weiterhin registriert sind.
- `docs/ROUTEN.md` dokumentiert 18 statt 21 verdrängte Handler und enthält die bereinigten Zeilennummern.
- Keine URL, Discord-ID, `custom_id`, Berechtigung, aktive Route oder Datenstruktur wurde geändert.
- Keine Datenbankmigration, keine Laufzeitdatenänderung und kein VPS-Deployment.

## CI

Die vollständigen CI-Ergebnisse werden im zugehörigen Pull Request dokumentiert.
