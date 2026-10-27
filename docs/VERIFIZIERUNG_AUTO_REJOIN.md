# Verifizierung per Discord-Autorisierung und Auto-Rejoin

Code: `pulse_rejoin.py` (Logik, Speicherung, Job), `cogs/rejoin.py` (Listener, stündlicher Loop, `/rejoin-abmelden`),
`cogs/verification.py` (Knopf), `pulse_admin.py` (`/verify/authorize`, `/admin/rejoin/*`), `webserver.py` (`/callback`).
Tests: `tests/test_rejoin.py` (nur Mocks, keine echten Discord-Aufrufe).

## Verhalten

- **Standardmäßig aus.** Admins schalten es unter Verwaltung → Datenschutz ein. Aktivieren wird verweigert, solange
  `DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET`, `DISCORD_TOKEN` oder eine `https://`-`PUBLIC_BASE_URL` fehlen.
- **Aktiv:** Der Knopf im Verifikations-Panel ist ein Link-Knopf direkt auf Discords eigenes Autorisierungsfenster
  (Scopes `identify guilds.join`, kein `email`). Es gibt keine Extra-Nachricht und keine Hinweisseite; der kurze Hinweis
  (`pulse_rejoin.PANEL_NOTE`) steht im Panel selbst. Ein statischer Link kann kein Cookie setzen, darum nutzt er einen
  signierten State (`pv1.…`, HMAC mit dem Session-Geheimnis). Die Bindung an den Browser ist hier unnötig: Verifiziert wird
  immer genau das Konto, das autorisiert. Nach „Autorisieren“ leitet Discord kurz auf `/callback` (Seite „Verifizierung
  abgeschlossen“ mit „Zurück zu Discord“). Speichern unter Verwaltung → Datenschutz veröffentlicht das Panel automatisch neu.
  `/verify/authorize` mit Einwilligungsseite bleibt als Alternative erhalten; alte Panels mit dem Interaktionsknopf antworten
  mit dem Link und dem kurzen Hinweis.
- Bereits Verifizierte, die noch nicht autorisiert haben, bekommen beim Klick den Hinweis und den Link, ihre Rollen bleiben.
- **Auto-Rejoin (stündlich, Job „Auto-Rejoin (stündlich)“):** Wer den Server freiwillig verlässt, wird frühestens
  24 Stunden später per `PUT /guilds/{guild_id}/members/{user_id}` mit dem gespeicherten Access-Token (bei Bedarf vorher
  erneuert) und der Bürgerrolle wieder hinzugefügt. Höchstens 25 Personen pro Lauf, höchstens ein Versuch pro Person und Stunde,
  nach 5 Fehlversuchen in Folge wird aufgegeben.
- **Nie zurückgeholt:** Gebannte (Bann-Event oder `fetch_ban` vor jedem Versuch; ist der Bannstatus nicht prüfbar, kein Rejoin),
  Gekickte (Audit-Log-Eintrag innerhalb von 30 s) und Abgänge mit ungeklärtem Grund (z. B. ohne Audit-Log-Recht).
- **Widerruf:** `invalid_grant`/401 beim Erneuern oder 401 beim Beitritt löscht die gespeicherten Tokens sofort; es gibt
  keinen weiteren Versuch. Audit-Eintrag „Rejoin-Autorisierung entfernt“.
- **Rate-Limits:** 429 beendet den Lauf sofort; 403/404 zählen als Fehlversuch; drei Timeouts/Verbindungsfehler in Folge beenden den Lauf.

## Opt-out und Datenschutz

- Mitglieder: `/rejoin-abmelden` (widerruft bei Discord und löscht die Zeile) oder „Deautorisieren“ in den Discord-Einstellungen
  (der nächste Lauf erkennt `invalid_grant` und löscht).
- Admins: Liste unter Verwaltung → Datenschutz mit Löschknopf pro Person; Datenauskunft enthält `rejoin_authorization` ohne Tokens.
- Audit-Log: Aktivieren/Deaktivieren, Löschen durch Admin oder Person, Widerruf, jeder Rejoin-Lauf mit Ergebnis.

## Speicherung

Neue Tabelle `rejoin_oauth_tokens` in `pulse.db` (additiv, `CREATE TABLE IF NOT EXISTS`, keine Änderung bestehender Tabellen).
Access- und Refresh-Token sind mit PyNaCl verschlüsselt; der Schlüssel wird aus dem Session-Geheimnis abgeleitet (eigene
Ableitung, getrennt vom Backup-Recovery). Ändert sich das Session-Geheimnis, sind die Tokens unlesbar und werden beim nächsten
Versuch gelöscht – die Personen müssen erneut autorisieren. Einstellung: `settings`-Schlüssel `rejoin_config`.

## Voraussetzungen auf Discord

Das Bot-Konto braucht auf dem Server „Soforteinladung erstellen“ (API-Voraussetzung für den Beitritt), „Rollen verwalten“
(Bürgerrolle unter der höchsten Pulse-Rolle), „Mitglieder bannen“ oder „Audit-Log anzeigen“ zum Prüfen von Bann/Kick.

## Hinweis Discord Developer Policy

Ein automatisches Zurückholen von Personen, die absichtlich gegangen sind, kann gegen die Discord Developer Policy verstoßen.
Pulse mindert das Risiko durch den Einwilligungstext vor der Autorisierung, 24 Stunden Karenz, Ausschluss von Bann/Kick und das
jederzeitige Opt-out. Die Entscheidung, alle Verifizierten einzubeziehen, hat die Serverleitung getroffen.
