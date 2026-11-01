# Phase 14.6a – Strenge Validierung vollständiger Backup-Archive

**Basis:** `0ea0b46360236b8c513fc1c56d7e826ca52a767b`  
**Branch:** `phase/14-6a-strict-backup-archive-validation`

## Änderungen

`pulse_backup.read_manifest` lehnt nicht unterstützte Manifest-Formate, falsche Typen und fehlende Dateilisten explizit ab.

`pulse_backup.verify_full_backup` prüft vor jedem Restore:
- Genau ein `manifest.json` und keine doppelten ZIP-Mitgliedsnamen.
- Keine doppelten Dateinamen in der Manifest-Dateiliste.
- Jede deklarierte Datei hat einen sicheren, nicht leeren Pfad.
- Jedes Ziel gehört zu den von Pulse unterstützten SQLite-, JSON-, Upload-, Attachment-, Transcript- oder Wartemusik-Pfaden; `discord_structure.json` bleibt als separates Strukturformat erlaubt.
- Dateigröße im Manifest stimmt mit der ZIP-Metadaten-Größe überein.
- SHA-256 ist syntaktisch gültig und stimmt bytegenau mit dem Inhalt überein.
- Keine zusätzlichen ZIP-Dateien, die nicht im Manifest deklariert sind.

Die Typentscheidung für DB/JSON/Files bleibt wie bisher pfadbasiert, nicht manifestbasiert.

## Neue Tests

- Doppelte ZIP-Mitgliedsnamen werden abgewiesen; Restore startet nicht.
- Nicht deklarierte ZIP-Dateien werden erkannt.
- Unbekannte Wiederherstellungsziele werden abgewiesen.
- Falsche Dateigröße und ungültige SHA-256-Metadaten werden erkannt.
- Ein Manifest, das kein Objekt mit Dateiliste ist, wird sicher abgewiesen.

## Sicherheit und Grenzen

- Keine bestehende gültige Backup-Datei wird verändert.
- Kein Restore wird während der Validierung ausgeführt.
- Keine Datenbank-/JSON-Schemata oder Laufzeitdaten geändert.
- Keine URL, Discord-ID, `custom_id` oder Berechtigung verändert.
- Kein VPS-Deployment.
