# Öffentliche Website BOCHUM RP

Stand: technischer Ausbau im Feature-Branch `feature/public-site-complete`. Die Website wird vom bestehenden Pulse-Webserver ausgeliefert, bleibt aber eine eigenständige öffentliche Oberfläche. Das Teamdashboard ist weiterhin getrennt und geschützt.

## Öffentliche Seiten

| Route | Inhalt |
|---|---|
| `/` | Öffentliche Startseite |
| `/regelwerk` | Freigegebene Ingame- und Discord-Regeln |
| `/fraktionen` | Ausschließlich offizielle Fraktionen |
| `/team-vorstellung` | Nur ausdrücklich veröffentlichte öffentliche Teamprofile |
| `/news`, `/news/{id}` | News-/Blog-Übersicht und einzelne Beiträge |
| `/faq` | Häufige Fragen |
| `/nutzungsbedingungen` | Community-/Nutzungsbedingungen; bis zur Betreiberfreigabe als Entwurf markiert |
| `/impressum`, `/datenschutz`, `/cookies`, `/sicherheit` | Rechtliche Informationen und Sicherheitsseite |
| `/sitemap.xml` | Sitemap, nur nach vollständiger Freigabe |
| `/team-login` | Separater Login-Einstieg für Teammitglieder |
| `/apply` | Vorhandenes Bewerbungsportal |

`/team`, `/dashboard` und alle übrigen internen Pulse-Routen bleiben geschützt. Die öffentliche Teamseite ist keine Ansicht der internen Teamliste.

## Öffentliche Teamprofile

Die öffentliche Teamvorstellung wird aus den aktuell konfigurierten TeamBot-Rollen erzeugt. Angezeigt werden ausschließlich Mitglieder, für die eine versionsgebundene, freiwillige Freigabe gespeichert ist und deren aktueller Rollenumfang weiterhin zur Freigabe passt.

- Die Freigabe wird vom jeweiligen Mitglied über `/teamseite_freigabe` erteilt und kann über `/teamseite_freigabe_widerrufen` beendet werden.
- Ohne aktuelle Freigabe, nach Widerruf, bei geändertem Rollenumfang oder nach Serveraustritt wird kein öffentliches Profil angezeigt.
- Öffentlich erscheinen nur Discord-Anzeigename und aktuelle Teamrolle sowie die daraus abgeleitete Team-Kategorie.
- Profilbilder werden nicht eingebunden. Die Karte verwendet lokal gerenderte Initialen; der Browser fordert dafür keine Ressourcen von Discord-CDN-Domains an.
- Discord-ID, Textversion, Rollenumfang sowie Erteilungs- und gegebenenfalls Widerrufszeitpunkt werden intern als minimaler Freigabenachweis gespeichert. Dieser Nachweis ist nicht Teil der öffentlichen Seite; seine Aufbewahrungsfrist muss noch festgelegt und technisch umgesetzt werden.
- Präsenzstatus, Abwesenheiten, Verwarnungen, interne Notizen, private Kontaktdaten und interne Berechtigungen werden nicht öffentlich dargestellt.

## News und Blog

Administratoren pflegen Beiträge unter `/admin/website`. Jeder Beitrag hat Titel, Kategorie, Veröffentlichungsdatum und Text sowie eine eigene Seite unter `/news/{id}`. Die Datenbankmigration für die Kategorie ist additiv und ergänzt vorhandene News mit der Kategorie „Community“. Ein Titelbild wird derzeit bewusst nicht von extern geladen.

## Cookies und Einwilligung

Die öffentliche Website lädt standardmäßig keine Drittanbieterinhalte, Analyse-, Marketing- oder Einbettungsdienste und speichert beim bloßen Seitenaufruf nichts im Browser. Daher wird kein künstlicher Cookie-Banner angezeigt. Die Cookie-Seite dokumentiert die tatsächlich genutzten Cookies und Browser-Speicher im Team-Login, Bewerbungsportal und Dashboard.

Soll ein optionaler Dienst ergänzt werden, muss er vor Zustimmung blockiert bleiben, in `OPTIONAL_SERVICES` registriert werden und ausschließlich nach der gespeicherten Auswahl geladen werden. Die vorhandene Consent-Logik bietet „Alle akzeptieren“, „Nur notwendige“ und individuelle Auswahl; die Einstellung lässt sich über die Cookie-Seite erneut öffnen. Kein Dienst darf per Default aktiviert werden. CSP und Datenschutzhinweise sind im selben PR zu aktualisieren.

## Betreiberangaben und Veröffentlichungssperre

Die folgenden Variablen werden ausschließlich auf dem VPS in der privaten `.env` gepflegt. Keine echten Werte in GitHub, Code, Issues oder Screenshots veröffentlichen.

```dotenv
PULSE_LEGAL_NAME=
PULSE_LEGAL_ADDRESS=
PULSE_LEGAL_EMAIL=
# Optional nur, falls der Hostingvertrag einen anderen rechtlichen Anbieter nennt:
# PULSE_LEGAL_HOSTER=
PULSE_LEGAL_REVIEWED=0
PULSE_TERMS_APPROVED=0
PULSE_TERMS_EFFECTIVE_DATE=
```

Indexierung und Sitemap werden erst freigeschaltet, wenn die drei Betreiberangaben ausgefüllt und die drei Freigabewerte bewusst gesetzt sind. Der bekannte Hostinganbieter wird standardmäßig aus der verifizierten Anbieterkennzeichnung übernommen; `PULSE_LEGAL_HOSTER` ist nur ein optionaler Override, falls der konkrete Hostingvertrag eine andere juristische Person nennt:
- `PULSE_LEGAL_REVIEWED=1`: Betreiber hat Datenschutzerklärung, Impressum, Datenflüsse, Fristen und Rechtsgrundlagen geprüft.
- `PULSE_TERMS_APPROVED=1`: Betreiber hat die Nutzungsbedingungen ausdrücklich freigegeben.
- `PULSE_TERMS_EFFECTIVE_DATE=YYYY-MM-DD`: tatsächliches Inkrafttretensdatum.

Diese Schalter sind keine Rechtsprüfung durch die Software und kein Rechtsgutachten. Sie dokumentieren ausschließlich die manuelle Betreiberentscheidung. Solange einer der Werte fehlt, liefern öffentliche Seiten `noindex, nofollow`, `robots.txt` sperrt die Indexierung und `sitemap.xml` ist nicht abrufbar.

Die Datenschutzerklärung benennt den aktuellen VPS-Anbieter als **Henrik Kramer e.K. (Prepaid-Hoster.de; Vionity-Kundenportal)** mit der in dessen Anbieterkennzeichnung veröffentlichten Geschäftsanschrift in Grömitz. Vionity ist das Verwaltungsportal, nicht der Betreiber von BOCHUM RP. Im Kundenbereich des konkreten VPS wird Offenbach am Main als Standort angezeigt. Ein abgeschlossener AVV ist noch nicht nachgewiesen; die Anbieter-FAQ beschreibt eine Anfrage und Einzelfallprüfung. Vor einer endgültigen Datenschutzfreigabe ist der unterzeichnete Vertrag samt Unterauftragnehmern/Standorten einzuholen.

Den Freigabestatus sieht ein Administrator unter `/admin/website`.

## Domain und HTTPS

Eine trycloudflare-Quick-Tunnel-Adresse ist nur temporär und hat keine Uptime-Garantie. Für die dauerhafte Website wird eine registrierte Domain und eine dauerhaft laufende, benannte Tunnel-/Reverse-Proxy-Konfiguration benötigt.

Wenn `bochumrp.de` bei netcup tatsächlich registriert und verwaltbar ist:

1. Registrierung/Domainstatus im netcup CCP bestätigen. Eine Bestellbestätigung ist noch kein Nachweis der abgeschlossenen Registrierung.
2. DNS passend zum gewählten Ziel einrichten. Nicht gleichzeitig einen zweiten Reverse Proxy starten, bevor Ports 80/443 und bestehende Proxy-Dienste geprüft wurden.
3. `PULSE_DOMAIN`, `PUBLIC_BASE_URL` und `DISCORD_REDIRECT_URI` in der privaten `.env` auf die endgültige Domain setzen.
4. Die Discord OAuth-Redirect-URI im Discord Developer Portal exakt ergänzen. Für Bewerbungs-/Recovery-OAuth die tatsächlich verwendete Anwendung separat prüfen.
5. HTTPS für die Domain einrichten und die öffentliche Seite, OAuth-Login und geschützte Routen extern testen.

## Bereitstellung und Sicherheit

- Öffentliche Seiten sind servergerendert; CSS/JS werden lokal ausgeliefert.
- Strenge Content-Security-Policy, X-Frame-Options und Rate-Limits bleiben aktiv.
- `/healthz` und `/health` dürfen nicht über einen öffentlichen Reverse Proxy offengelegt werden.
- Keine Freigabe von `/team`, `/dashboard`, internen APIs oder Admin-Routen.
- Änderungen aus diesem Ausbau erst nach grünen Tests und Review mergen. Deployment/Domain/DNS/VPS-Änderungen separat und nur mit ausdrücklicher Freigabe.
