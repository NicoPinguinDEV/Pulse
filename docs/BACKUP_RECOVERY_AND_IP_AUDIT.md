# Backup-Recovery und Dashboard-IP-Audit

## Zweck und Sicherheitsgrenzen

Pulse kann freiwillig freigegebene Mitglieder bei einem Notfall einem separaten Backup-Discord-Server hinzufügen. Discord bietet kein echtes Verschieben zwischen Servern: Nutzer bleiben auf dem Hauptserver, bis sie ihn selbst verlassen oder dort entfernt werden. Der Recovery-Sync entfernt niemanden aus einem Server.

Für den Backup-Beitritt autorisiert jedes Mitglied den OAuth2-Zugriff `identify guilds.join` ausdrücklich auf einer Discord-Freigabeseite. Die normale Schaltflächen-Verifikation erteilt diese Zusatzfreigabe **nicht automatisch**. Nach der Verifikation wird bei aktivierter Funktion ein optionaler Link angezeigt. Ohne ausdrückliche Freigabe wird niemand in die Recovery-Liste aufgenommen.

Die Funktion übernimmt keine Rollen aus dem Hauptserver. Neue Backup-Mitglieder bekommen ausschließlich die konfigurierte Bürgerrolle. Pulse blockiert die Aktivierung, wenn diese Rolle Administrator- oder Moderationsrechte enthält. Der Befehl entfernt keine bestehenden Rollen oder Mitglieder; prüfe daher den Backup-Server und die Bürgerrolle vor der Aktivierung. Das Bot-Konto muss auf dem Backup-Server Mitglied sein, „Rollen verwalten“ und „Soforteinladung erstellen“ besitzen und in der Rollenliste über der Bürgerrolle stehen.

## Einrichtung

1. Lade Pulse als Bot in den gewünschten Backup-Server ein.
2. Erstelle dort eine eigene Bürgerrolle mit den normalen Mitgliedsrechten, aber ohne Administrator-, Moderations-, Rollen-, Kanal-, Webhook- oder Nachrichtenverwaltungsrechte.
3. Stelle sicher, dass `PUBLIC_BASE_URL` und `DISCORD_REDIRECT_URI` auf deine öffentliche HTTPS-Domain zeigen und die Callback-URL `/callback` im Discord Developer Portal registriert ist.
4. Öffne im Hauptserver das Dashboard → **Verwaltung → Datenschutz**. Trage die Backup-Server-ID und die ID der Bürgerrolle dieses Backup-Servers ein und aktiviere die Funktion. Pulse validiert dabei den Server, die Rolle, ihre Rechte und die Rollen-Hierarchie.
5. Mitglieder klicken nach erfolgreicher Verifikation freiwillig auf „Backup-Zugang freischalten“ und bestätigen die zusätzlichen OAuth-Rechte bei Discord.

## Wiederherstellung

Führe im Hauptserver oder im Backup-Server als Discord-Administrator den Befehl `/recovery-sync` aus. Ohne die zusätzliche Bestätigung wird nichts geändert. Setze `confirm: True` und optional die Anzahl `max_users` (1–100). Der Befehl verarbeitet pro Lauf höchstens die festgelegte Anzahl freiwillig freigegebener Konten. Er kann auch direkt auf dem Backup-Server ausgeführt werden, wenn der Hauptserver nicht mehr verfügbar ist und Pulse weiterhin im Backup-Server ist.

Pulse speichert OAuth-Recovery-Tokens verschlüsselt mit PyNaCl und einem aus dem bestehenden dauerhaften Pulse-Session-Geheimnis abgeleiteten Schlüssel. Deshalb muss `SESSION_SECRET` nach Einrichtung stabil bleiben bzw. die Datei `.session_secret` erhalten bleiben. Bei widerrufener oder abgelaufener Discord-Freigabe wird der gespeicherte Zugang entfernt und die Person muss ihn erneut freigeben.

Mit `/recovery-abmelden` kann ein Mitglied die OAuth-Freigabe beenden und den lokal gespeicherten Recovery-Zugang löschen. Der Befehl entfernt das Mitglied nicht automatisch aus dem Backup-Server; das Mitglied kann diesen bei Bedarf selbst verlassen.

## IP-Anmeldeprotokoll

Pulse protokolliert nach einem erfolgreichen Team-Dashboard-OAuth-Login die vom vertrauenswürdigen Verbindungsweg gemeldete IP-Adresse, den Zeitpunkt, die Discord-ID/den angezeigten Namen und eine gekürzte Browserkennung. Normale Seitenaufrufe, Bewerbungs-Logins und fehlgeschlagene Logins werden nicht in dieser Tabelle protokolliert. Die letzten 100 Anmeldungen sind nur für Dashboard-Administratoren unter **Verwaltung → Datenschutz** sichtbar; das Anzeigen der Liste wird im Pulse-Audit-Log festgehalten. Veraltete Einträge werden bei einer nachfolgenden Anmeldung oder beim Öffnen der Datenschutzansicht nach 30 Tagen bereinigt. Die Tabelle ist zusätzlich auf 10.000 Einträge begrenzt.

Eine IP-Adresse beweist nicht zuverlässig den tatsächlichen Aufenthaltsort einer Person: VPNs, Proxys, Mobilfunknetze und dynamische IPs können das Ergebnis verfälschen. Pulse blockiert deshalb nicht automatisch Länder und ruft keine externen IP-Geolokationsdienste auf. Informiere Teammitglieder vor Aktivierung transparent über Zweck, Zugriff und Speicherfrist und kläre die dafür geltende Datenschutzgrundlage für deinen Server.

## Grenzen

- Der Backup-Server muss separat abgesichert und regelmäßig gesichert werden.
- Kein Bot kann garantieren, dass Nutzer vor Administratoren oder dem Serverinhaber des jeweiligen Servers geschützt sind.
- Der Recovery-Sync darf nur freiwillig autorisierte Nutzer erneut hinzufügen; er ist keine Umgehung eines Discord-Banns oder einer widerrufenen OAuth-Freigabe.
