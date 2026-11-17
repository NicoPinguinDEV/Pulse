# Recovery-Schlüssel (AUTH-2)

Die Discord-Refresh-Tokens der Backup-Wiederherstellung werden mit einem **eigenen Schlüssel** verschlüsselt, der
unabhängig vom Session-Secret ist (`PULSE_RECOVERY_KEY`). Ein Leck oder eine Rotation des Session-Secrets berührt
die Recovery-Freigaben damit nicht mehr.

## Einrichten
1. Schlüssel erzeugen (mindestens 32 zufällige Zeichen):
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. In die `.env` eintragen: `PULSE_RECOVERY_KEY=<Wert>` und den Wert **getrennt** sichern (Passwortmanager). Der
   Schlüssel ist nicht Teil der Pulse-Backups; ohne ihn sind gespeicherte Freigaben nicht lesbar.
3. Pulse neu starten.

Ohne gültigen Schlüssel (fail-closed): neue Freigaben werden abgelehnt (`/recovery/authorize/start` → 503), gespeicherte
Freigaben werden **nicht benutzt** (Discord rotiert Refresh-Tokens bei Benutzung; der Ersatz ließe sich nicht speichern).

## Format
`v2:<Schlüssel-ID>:<Base64>` (Schlüssel-ID = erste 8 Hex-Zeichen eines SHA-256 des abgeleiteten Schlüssels).
Der Schlüssel wird per HKDF-SHA256 abgeleitet. Alte Einträge (ohne Präfix, vom Session-Secret abgeleitet) bleiben
**lesbar**, solange das Session-Secret unverändert ist – es wird nichts automatisch umgeschrieben.

## Bestehende Einträge migrieren (separater, manueller Schritt)
1. Datenbank sichern (Backup-Center).
2. Prüfen, was passieren würde (ändert nichts):
   `python -c "import pulse_recovery as r; print(r.migrate_to_current_key())"`
3. Ausführen:
   `python -c "import pulse_recovery as r; print(r.migrate_to_current_key(dry_run=False))"`
   Jeder Eintrag wird entschlüsselt, neu verschlüsselt und **vor dem Schreiben erneut entschlüsselt**; nicht lesbare
   Einträge bleiben unverändert (`unreadable` im Ergebnis).
4. Zustand ansehen: `python -c "import pulse_recovery as r; print(r.encryption_status())"`
   (`current` / `old-key` / `legacy` / `unreadable`).

## Schlüsselwechsel (Rotation)
1. Neuen Schlüssel erzeugen. Den **alten** Wert nach `PULSE_RECOVERY_KEY_PREVIOUS`, den neuen nach `PULSE_RECOVERY_KEY`.
2. Neu starten; alte Einträge bleiben über den vorherigen Schlüssel lesbar (`old-key`).
3. `migrate_to_current_key(dry_run=False)` ausführen, bis `old-key` und `legacy` bei 0 sind.
4. `PULSE_RECOVERY_KEY_PREVIOUS` entfernen.

## Wenn Einträge unlesbar sind
`encryption_status()["unreadable"] > 0` bedeutet: Schlüssel fehlt oder wurde gewechselt, ohne den alten als
`PULSE_RECOVERY_KEY_PREVIOUS` zu behalten. Betroffene Personen können mit `/recovery-abmelden` ihre Freigabe löschen
(Pulse sagt dann ehrlich, dass Discord nicht informiert werden konnte) und sich neu freigeben.
