# Abmeldungen: automatische Bereinigung

Abmeldungen (`abmeldungen.db`, Tabelle `abmeldungen`) enden automatisch, wenn ein Mitglied das Team oder den Server verlässt. Die Logik liegt zentral in `AbmeldungCog.cleanup_member_absence` (`cogs/abmeldung.py`); es gibt keine zweite Verwaltung und keine Schemaänderung.

## Auslöser

| Ereignis | Aufrufstelle | Mitglied noch da? |
| --- | --- | --- |
| Erfolgreicher Team-Kick (Dashboard, Zweitfreigabe, Warn-Team-Kick) | `webserver.apply_team_kick_role_reset` nach erfolgreichem Rollenwechsel (auch „bereits nur Bürger-/Ex-Teamler-Rolle“) | ja |
| Letzte konfigurierte Teamrolle verloren | `AbmeldungCog.on_member_update` (Übergang „hatte Teamrolle“ → „keine“) | ja |
| Serveraustritt, Kick | `AbmeldungCog.on_member_remove` | nein |
| Ban | `AbmeldungCog.on_member_ban` | nein |

Kick/Ban lösen meist beide Events aus; das zweite findet keinen Eintrag mehr und tut nichts.

## Ablauf

1. Löschen in der Datenbank (zuerst, unabhängig von Discord). Filter: `user_id` und `guild_id` der betroffenen Guild. Alte Zeilen ohne Guild-ID werden nur entfernt, wenn das Mitglied in keiner anderen Pulse-Guild ist; ungültige Guild-ID löscht nichts. Datenbankfehler brechen kontrolliert ab, ohne Discord-Änderungen.
2. Nur bei vorhandenem Mitglied: `reset_user_status` (Abmeldungsrolle entfernen, Nickname-Suffix mit bestehender Schutzlogik zurücksetzen). Fehlende Rolle/Berechtigungen werden dort abgefangen.
3. Abmeldungsliste aktualisieren, bei vorhandenem Mitglied zusätzlich die Teamliste.

Ohne gelöschten Eintrag (Wiederholung, nie abgemeldet) passiert nichts. Fehler in Schritt 2/3 werden geloggt, ohne Abmeldungsgründe.

## Nicht betroffen

Ablaufprüfung (15 min), manuelle Rückmeldung (Discord/Dashboard), Formulare, Buttons (`custom_id`), Team-/Server-Kick-Freigaben, Checklisten, Ticketübergabe und Schichtende bleiben unverändert. Tests: `tests/test_absence_offboarding_cleanup.py`.
