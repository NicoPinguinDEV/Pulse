# Pulse Discord-Design, Willkommen/Abschied und Verwaltungsbereich

Stand: Oktober 2026 · Version laut `pulse_version.py`: 7.9.7.

## 1. Zentrales Embed-Designsystem (`pulse_embed_design.py`)

- **Semantische Farben** statt fest verdrahteter `discord.Color.*`: `info`/`brand`, `success`, `warning`, `danger`, `neutral`/`audit`, `highlight`.
- **Konfigurierbar** in *Verwaltung → Discord-Auftritt → Embed-Design* (Markenname, Footer, sechs Farben, „Standard wiederherstellen“). Gespeichert als Setting `discord_branding` in `pulse.db`; Cache 30 s, beim Speichern sofort invalidiert.
- **Bausteine:** `make_embed()`, `build_embed_spec()` (rein, testbar), `to_discord_embed()`, `apply_brand_footer()`, `truncate()`, `neutralize_mentions()`, `discord_timestamp()`, `validate_template()`/`render_template()`, `safe_image_url()` (nur `https://` bzw. `attachment://`).
- **Discord-Limits** (Titel 256, Beschreibung 4096, 25 Felder à 256/1024, Footer 2048, gesamt 6000) werden im Builder erzwungen.

### Nachrichten-Inventar (Embeds je Datei) und Migrationsstand

| Datei | Embeds | Farben zentral | Bemerkung |
|---|---|---|---|
| `webserver.py` (Teamaktionen, Warns, Beförderungen, Team-Updates) | 6 + 31 Farbstellen | ✅ | |
| `cogs/pulse_pro.py` (Ankündigungen, Team-Kick, Aufgaben …) | 8 | ✅ | |
| `cogs/voice_support.py` (Bereitschaft, Fälle, Protokoll, Bewertungen) | 7 | ✅ | |
| `cogs/tickets.py` (Panels, Begrüßung, Status, Transkript) | 7 | ✅ | Panel-/Begrüßungsfarbe bleibt im Panel-Builder wählbar |
| `cogs/pulse_suite.py`, `cogs/advanced_logs.py`, `cogs/buero_system.py`, `cogs/ai_moderation.py`, `cogs/activity_check.py`, `cogs/backups.py`, `cogs/abmeldung.py`, `cogs/announcement_archive.py`, `cogs/flaggenquiz.py`, `cogs/honeypot.py`, `cogs/rp_verwaltung.py`, `cogs/teamliste.py`, `pulse_ultimate.py`, `pulse_pro.py` | 1–5 je Datei | ✅ | |
| `cogs/social_promo.py` Video-Ankündigung | 1 | bewusst nein | Plattformfarbe (YouTube-Rot, Twitch-Lila …) ist Teil der Wiedererkennung |
| `cogs/welcome.py` (neu) | 1 | ✅ | vollständig über `build_embed_spec` |

Alle 116 bisher fest verdrahteten Farbaufrufe in 19 Dateien nutzen jetzt die Palette (`_pulse_color("…")`). **Noch nicht vereinheitlicht:** Titel-/Feldstruktur und Footer der bestehenden Embeds – sie behalten ihre bisherigen Texte. Kurze ephemere Antworten bleiben bewusst Text.

### Fortsetzungsplan (nächste Etappe)
1. Gruppe für Gruppe `apply_brand_footer()` bzw. `make_embed()` in Team-Aktionen (`webserver.py`), Voice-Support und Tickets einsetzen, ohne Custom-IDs/Views zu ändern.
2. Logs (`advanced_logs.py`) auf Audit-Layout (Actor/Target-Felder, Zeitstempel) umstellen.
3. Danach erneute Suche nach `discord.Embed(` ohne Footer.

### Stil „Modern“ (Components V2, wie GalaxyBot)
- Standard seit Oktober 2026: Nachrichten erscheinen als Discord-**Container** statt als Embed – Überschrift, Text, Profilbild rechts, Banner als Bild-Galerie, Trennlinie und kleine Fußzeile; optional farbiger Rand links.
- Baustein: `layout_from_spec()` in `pulse_embed_design.py` (gleiche Daten wie das Embed, Textgrenze 4000 Zeichen wird eingehalten).
- Umgestellt: Willkommen, Abschied, Verifikations-Panel (Button sitzt im Container, persistent nach Neustart). Umschaltbar in *Verwaltung → Discord-Auftritt → Embed-Design → Nachrichten-Stil*.
- Discord kann eine Nachricht nicht zwischen Embed und Container umwandeln: Beim Stilwechsel ersetzt „Panel veröffentlichen“ das alte Verifikations-Panel genau einmal.
- Noch nicht umgestellt: Tickets, Teamaktionen, Logs, Voice-Support (nächste Etappe mit demselben Baustein).

## 2. Willkommen & Abschied

- **Bot:** `cogs/welcome.py` – einziger Sender für Beitritt/Austritt; 60-s-Duplikatschutz pro Mitglied, keine Wiederholungsversuche, fehlende Rechte/Kanäle werden höchstens stündlich protokolliert.
- **Logik:** `pulse_welcome.py` – Einstellungen (Setting `welcome_config`), Platzhalter `{user} {display_name} {username} {user_id} {server_name} {member_count} {account_created} {joined_at}`, unbekannte Platzhalter blockieren das Speichern.
- **Darstellung:** Profilbild als Thumbnail (oben rechts), Banner als Embed-Bild (unten). Hochgeladene Banner liegen in `uploads/welcome_banners/` (nicht in Git) und werden als Anhang mitgesendet – keine öffentliche URL nötig. Alternativ eine `https://`-Bild-URL.
- **Pings:** Nur das neue Mitglied, und nur wenn „Erwähnen“ aktiv ist (`allowed_mentions`). `@everyone`/`@here`/Rollen werden aus Vorlagen und Namen entschärft. Abschied pingt nie.
- **Standard:** beide Nachrichten **aus**, bis ein Admin Kanal und Schalter setzt.

## 2a. Verifikation neuer Mitglieder

- **Rollen (vom Betreiber vorgegeben, fest im Code):** Unverifiziert `1532762237284913363` (beim Beitritt), Bürger `1531303733311307997` (nach Klick).
- **Ablauf:** Beitritt → Unverifiziert-Rolle. Klick auf „Jetzt verifizieren“ → Pulse vergibt **zuerst** die Bürgerrolle und entfernt **danach** die Unverifiziert-Rolle. Schlägt die Bürgerrolle fehl, bleibt die Schutzrolle bestehen. Rückmeldung immer ephemer.
- **Robust:** persistenter Button (`custom_id` `pulse:verify:v1`, nach Neustart funktionsfähig), Klick-Sperre je Mitglied, 5-s-Cooldown, 60-s-Duplikatschutz beim Beitritt, nur der konfigurierte Server, Rollen werden nie aus Nutzereingaben übernommen.
- **Voraussetzungen in Discord:** Pulse braucht „Rollen verwalten“, und die höchste Pulse-Rolle muss **über** beiden Rollen liegen. Kanalrechte so setzen, dass die Unverifiziert-Rolle nur Begrüßung, Regeln und Verifikation sieht – Pulse ändert Kanalrechte bewusst nicht automatisch.
- **Einrichtung:** Verwaltung → Discord-Auftritt → Verifikation: Kanal + Texte speichern → „Panel veröffentlichen“ → „Verifikation aktiv“ anhaken → speichern. Standard ist **aus**, damit niemand eingesperrt wird, bevor das Panel existiert.
- **Erinnerung:** Standardmäßig alle 60 Minuten (15–1440 einstellbar) erwähnt Pulse die Unverifiziert-Rolle im Verifikationskanal – nur wenn die Verifikation aktiv, das Panel veröffentlicht und mindestens eine Person unverifiziert ist. Vor jeder neuen Erinnerung wird die vorherige gelöscht, es steht also immer höchstens eine im Kanal. Zeitpunkt wird gespeichert, ein Neustart pingt nicht vorzeitig. Ist die Rolle nicht erwähnbar, braucht Pulse „@everyone, @here und alle Rollen erwähnen“.
- **Diagnose:** Rollenstatus und Rechteprobleme werden live angezeigt; die letzten 10 Fehler (ohne Namen) erscheinen unter „Letzte Probleme“.

## 3. Team und Verwaltung

- Sidebar-Umschalter **„👥 Team | 🛠 Verwaltung“**. Die Verwaltung erscheint nur, wenn mindestens ein Verwaltungsbereich erlaubt ist.
- **Verwaltung** (`/admin`): Discord-Auftritt, Ticket-Panels, Einstellungen & Rechte, Backups, Systemstatus (nur Administratoren); Ankündigungen und Social Media (Administratoren oder „Ankündigungen verwalten“).
- **Team:** alle bisherigen Arbeitsseiten (Mein Pulse, Tickets, Sprach-Support, Kalender, Abmeldungen, Command Center, Operations Suite …) unverändert.
- Alle Routen behalten ihre URLs; jede Verwaltungsroute prüft serverseitig (`auth(..., admin=True)` bzw. Ankündigungsrecht). Die Schnellsuche (Ctrl K) zeigt Verwaltungslinks nur Berechtigten.
- Tests: `tests/test_pulse_admin_area.py` (Teamler erhält 403 auf alle `/admin`-Routen inkl. POST, Speichern/Neuladen, Upload-Validierung, Pfad-Traversal, Branding-Reset).
