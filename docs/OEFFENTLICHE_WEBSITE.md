# Öffentliche Website: Veröffentlichung

Stand: vorbereitet, nicht ausgerollt. Domain, DNS und Discord-Einstellungen ändert nur der Betreiber.

## Aufbau

| Route | Inhalt |
|---|---|
| `/` | Öffentliche Startseite. Angemeldete Teammitglieder werden wie bisher zu ihrer Startseite geleitet. |
| `/regelwerk`, `/faq`, `/fraktionen`, `/news` | Öffentliche Inhalte |
| `/impressum`, `/datenschutz`, `/cookies`, `/sicherheit` | Rechtliches und Sicherheit |
| `/team-login` | Bisherige Login-Karte, Discord OAuth wie zuvor über `/login` |
| `/apply` | Bewerbungsportal, unverändert |
| `/team`, `/dashboard` und alle internen Seiten | Unverändert geschützt |
| `/admin/website` | News veröffentlichen, fehlende Angaben sehen (nur Administratoren) |

Inhalte stammen aus `support_knowledge.md` (Kapitel A und B), den offiziellen Fraktionen der Fraktionsverwaltung und der Tabelle `public_news`. Sie sind Teil des Komplett-Backups, weil sie in `pulse.db` liegen.

## Veröffentlichen

Voraussetzung: Domain, die auf den VPS zeigt, Ports 80 und 443 offen. Auf dem VPS läuft bereits ein Reverse Proxy? Dann nur einen von beiden Wegen nutzen, Port 80/443 können nicht doppelt belegt werden.

1. A-Eintrag (und AAAA, falls IPv6) der Domain auf den VPS setzen und abwarten, bis er auflöst.
2. In `.env` setzen, Werte aus `.env.example`:

   ```
   PULSE_DOMAIN=<deine-domain>
   PUBLIC_BASE_URL=https://<deine-domain>
   DISCORD_REDIRECT_URI=https://<deine-domain>/callback
   ```

   Dazu die Angaben aus [RECHTLICHE_FREIGABE.md](RECHTLICHE_FREIGABE.md).
3. Im Discord Developer Portal unter OAuth2 die Redirect-URI `https://<deine-domain>/callback` ergänzen. Bewerbungs- und Wiederherstellungs-URIs ebenso, falls eine eigene Anwendung genutzt wird (`DISCORD_APPLICATION_REDIRECT_URI`).
4. Deployment wie in [VPS_DEPLOYMENT.md](../VPS_DEPLOYMENT.md) beschrieben, inklusive Sicherung. Für das Caddy-Profil:

   ```
   docker compose --profile https up -d
   ```

   Caddy holt das TLS-Zertifikat von Let's Encrypt automatisch und leitet HTTP auf HTTPS um.
5. Prüfen: `https://<deine-domain>/` lädt mit gültigem Zertifikat, Team-Login funktioniert, `https://<deine-domain>/healthz` antwortet 404 (nur auf dem Host erreichbar).

## Sicherheit im Betrieb

- Der Container-Port bleibt an `127.0.0.1` gebunden, nach außen spricht nur Caddy.
- `/healthz` und `/health` sperrt Caddy. Ein Reverse Proxy außerhalb dieses Profils sollte das nachbilden. Zusätzlich erhalten Aufrufer von öffentlichen Adressen nur noch `{"ok": ...}` ohne Version, Speicher oder Jobnamen.
- `PULSE_FORWARDED_ALLOW_IPS` muss die Proxy-Adresse enthalten, sonst zählt die Begrenzung pro Adresse alle Besucher zusammen.
- Öffentliche Seiten laufen unter einer strengen Content-Security-Policy ohne Inline-Code und fremde Quellen.
- Bei Rechtsangaben: Erst wenn alle vier Pflichtfelder gesetzt sind, gibt `robots.txt` die öffentlichen Seiten frei.

## Ressourcen

Die Seiten sind Server-gerendert und ohne Framework. CSS und JS zusammen unter 20 KB, Fraktionen, News und Regelwerk werden 60 Sekunden im Speicher gehalten. Das RAM-Budget aus `CLAUDE.md` ändert sich dadurch nicht messbar.

## Schriften

Die Website lädt bewusst keine Webfonts von Dritten. Sie nutzt die DIN-Schrift Bahnschrift, wo vorhanden, sonst Systemschriften. Für ein einheitliches Bild auf allen Geräten lässt sich eine frei lizenzierte Schrift lokal unter `static/` ablegen.
