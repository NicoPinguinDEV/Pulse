# Phasenbericht F8 – Vorlagenbibliothek in den Aufrufpfaden (#123)

Re-Audit: 16.11.2026. Der ursprüngliche F8-Stand ist durch die späteren Änderungen aus PR #268 und Arbeitsbranch `fix/global-live-sync-177` ergänzt.
Audit #123 bleibt **Teilweise**: Ticketantworten, Ankündigungen und Bewerber-DMs sind angebunden; Moderationsgründe und Welcome-/Leave-Texte haben noch eigene Leser.

## 1. Bestandsaufnahme: tatsächliche Leser und Schreiber

| Vorlagenart | Speicher | Leser (Aufrufpfad) | Schreiber | Bibliothek nutzt |
|---|---|---|---|---|
| Ticket-Schnellantworten | Setting `ticket_quick_replies` (`pulse_ticket_extras`) | `/antwort` und Autocomplete (`cogs/ticket_tools.py`), Editor-Textfelder (`webserver.py` `/tickets/tools`) | `POST /tickets/tools/replies` (nur Admin) | **ja (F8)** |
| Bewerber-Nachrichten | Zentrale `message_templates` für aktive Versionen, Legacy-Setting als Fallback (`pulse_app_comms`) | `dm_applicant`, Gesprächs-/Erinnerungs-/Einstellungs-/Ablehnungs-Pfade in `webserver.py` | Bewerber-Editor synchronisiert beim Speichern/Wiederherstellen (`sync_library`); `/vorlagen` | **ja (neuer DM-Aufrufpfad)** |
| Moderationsgründe | Tabellen `mod_reason_templates` + `_versions` (`pulse_mod_reason_templates`) | `cogs/mod_cases.py` (Auswahl, `resolve_reason`, Verwaltung, Versionen) | `cogs/mod_cases.py` | nein (siehe 3.3) |
| Willkommen/Abschied | Setting `welcome_config` (`pulse_welcome`) | `cogs/welcome.py` (`build_member_message`) | `POST /admin/discord/welcome` (`pulse_admin.py`) | nein (siehe 3.4) |
| Ankündigungen | Zentrale `message_templates` (`announcement`) | Ankündigungsformular bietet Vorlagen aus `/vorlagen` an | `/vorlagen` | **Auswahl im Dashboard** |

Ticket-Schnellantworten, das Ankündigungsformular und Bewerber-DMs lesen inzwischen aus `message_templates`. Die Bibliotheksart `moderation_reason` wird im eigenen Aufrufpfad noch nicht verwendet.

## 2. Umsetzung: Ticket-Schnellantworten

Keine zweite Verwaltung: Die Seite „Ticket-Werkzeuge“ bleibt die Bearbeitungsoberfläche und schreibt weiter den bisherigen Speicher.

* `pulse_ticket_replies.py` (Verbraucher): `offers()` / `resolve()` lesen aus der Bibliothek, `sync_from_legacy()` spiegelt jeden Speichervorgang als neue **Version**.
* `cogs/ticket_tools.py`: `/antwort` und Autocomplete nutzen `offers`/`resolve`; nach dem Senden wird ein Ereignis `ticket.quick_reply_sent`
  (Vorlagen-ID, Version, Quelle, Korrelation `ticket:<id>`) geschrieben, die verwendete Version ist damit nachvollziehbar.
* `webserver.py` (3 Zeilen): `POST /tickets/tools/replies` ruft nach dem Speichern `sync_from_legacy`; ein Bibliotheksfehler blockiert das Speichern nie.
* `pulse_templates.py`: neu `lookup()` (Leser inkl. Archiv, ohne Zeilenkappe), `sync_kind()` (Abgleich mit Versionen), Konstante `TAKEOVER_ID`.

Entscheidungen und Semantik

| Anforderung | Umsetzung |
|---|---|
| Rechte beibehalten | gleiche Regel wie zuvor (`can_manage_tickets` oder Admin = Policy `ticket.manage`); ohne Recht wird nichts angeboten. Keine Erweiterung. |
| Guild-Isolation | Bibliothek je Server; Vorlagen eines fremden Servers sind unsichtbar. Der alte Speicher ist global (ein Server) und bleibt es. |
| Archivierte nicht anbieten | Archivierte Bibliotheksvorlagen werden nicht angeboten **und** nicht aus dem alten Speicher wiederbelebt (sie überdecken die Altzeile). |
| Versionen nachvollziehbar | Antwort enthält Vorlagen-ID und Version; Ereignis im Ticketverlauf. |
| Bestehende Platzhalter-Semantik | Bisher gab es **keine** Platzhalter: Texte wurden wörtlich gesendet. Legacy-Texte werden unverändert wörtlich gesendet. Bibliothekstexte kennen `{user}` (Name des Ticket-Erstellers, nie eine Erwähnung), `{ticket}`, `{category}`, `{staff}`. |
| Mention-Schutz | `@everyone`/`@here`/Rollen-Pings werden im Text und in eingesetzten Werten neutralisiert; zusätzlich bleibt `AllowedMentions(roles=False, everyone=False)`. |
| Fehlende Vorlage | kontrollierte Meldung (`ticket_quick_reply_missing`), kein Ereignis. Nicht renderbar (z. B. zwischenzeitlich archiviert) zählt als fehlend. |
| Legacy-Fallback | Leere oder fehlerhafte Bibliothek: alter Speicher antwortet. Nicht überführte Altantworten werden weiter angeboten. Keine automatische Migration; die Bibliothek füllt sich beim Speichern der Seite oder durch `import_legacy`. |
| Ungültige Platzhalter | Beim Speichern nicht in die Bibliothek aufgenommen und gemeldet; eine ältere Bibliotheksversion dieses Titels wird archiviert (Marker `legacy-sync`), damit der neue Alt-Text nicht von einer veralteten Version überdeckt wird. |
| Konkurrierende Änderung | Version wird mit `expected_version` geprüft; Konflikt wird im Abgleich gemeldet, nichts wird überschrieben. |

Bekannte Einschränkungen

* Reihenfolge: Bibliothekstexte erscheinen je Bereich alphabetisch, der alte Speicher behielt die gespeicherte Zeilenreihenfolge. Die Obergrenze 25 (Discord-Autocomplete) gilt weiter.
* Zwei Wahrheiten bis zum Ende der Übergangszeit: Wird die Bibliothek ohne die Seite bearbeitet (es gibt dafür keine Oberfläche), überschreibt das nächste Speichern der Seite den Text mit einer neuen Version. Das ist nachvollziehbar, aber bewusst nicht verhindert.
* Der Editor zeigt weiterhin den alten Speicher; Versionen und Archiv sind über die Bibliothek lesbar, es gibt noch keine Anzeige.

## 3. Integrationsanalyse der weiteren Vorlagenarten (nicht umgesetzt)

### 3.1 Bewerber-Nachrichten – angebunden, mit Legacy-Fallback

* `pulse_app_comms.resolve_for_guild` liest pro Vorlagenschlüssel die aktive zentrale Bibliotheksversion. `dm_applicant`, Gesprächseinladungen, Zusagen/Absagen und Gesprächserinnerungen verwenden diesen Resolver.
* `POST /applications/templates` und das Wiederherstellen einer Legacy-Version rufen `sync_library` auf. Jede Änderung wird in der zentralen Bibliothek als neue Version gespiegelt; der alte Speicher bleibt als Rückfall verfügbar.
* Das Bewerber-Editorformular zeigt aktive zentrale Texte an. Die zentrale Verwaltungsseite `/vorlagen` bleibt ebenfalls nutzbar.
* Der Bewerber-Kommunikationsjournal-Eintrag enthält jetzt die zentrale Vorlagen-ID und Versionsnummer. Ein Bibliotheksfehler blockiert das Versenden nicht; dann wird die bisherige Legacy-Vorlage verwendet.
* Manuell archivierte zentrale Vorlagen fallen auf den eingebauten Standardtext zurück und werden nicht versehentlich aus dem Legacy-Speicher reaktiviert. Nur mit `TAKEOVER_ID` markierte Archive erlauben ausdrücklich den Legacy-Fallback.
* Die Bibliotheksleseoperation nutzt serverseitigen `application.manage`-Kontext; Schreibvorgänge bleiben an die bestehende Admin-Prüfung des Bewerber-Editors und die zentrale Policy gebunden. Keine ID-Migration oder Änderung bestehender Bewerbungsdatensätze.

### 3.2 Ankündigungen – Dashboard-Auswahl angebunden

* Die Seite `/announcements` blendet über `pulse_templates_pages.announcement_picker` Vorlagen aus der Bibliothek ein. Veröffentlicht wird weiterhin über den vorhandenen Ankündigungsweg; die Vorlage wird beim Verfassen ausgewählt.
* Der Discord-Befehl `/teamannounce` besitzt weiterhin keinen Bibliotheks-Auswahlparameter. Die Bibliotheksart `announcement` ist damit im Dashboard angebunden, aber noch nicht in allen passenden Versandwegen.

### 3.3 Moderationsgründe – **eigener, datenverknüpfter Speicher; nicht ohne Migration überführbar**

* `b4` (`feature/automod-testmode-false-positives`) enthält **keine** Änderungen an Vorlagen (0 Treffer) und ändert weder `cogs/mod_cases.py` noch `pulse_mod_reason_templates.py`; Überschneidung nur in `pulse_db.py` (39 Zeilen) und `webserver.py` (2 Zeilen). Ein Konflikt ist deshalb gering, ein inhaltlicher Blocker besteht nicht.
* Hindernis ist ein anderes: `mod_cases` speichert `reason_template_id`/`reason_template_version` (IDs `MRT-…`). Ein Wechsel auf Bibliotheks-IDs (`TPL-…`) würde diese Verweise ungültig machen und verlangt eine Datenmigration – ausdrücklich ausgeschlossen.
* Rechte weichen ab: `pulse_mod_cases.may_moderate(action, perms, discord_perms)` prüft zusätzlich Discord-Rechte je Aktion; die Bibliotheksart verwendet `case.view.BM`. Die Zuordnung wäre eine Rechteänderung.
* Empfehlung: `pulse_mod_reason_templates` bleibt Speicher der Wahrheit. Die Bibliotheksart `moderation_reason` bleibt reserviert oder wird entfernt; eine spätere Lösung wäre ein lesender Spiegel, kein Umzug.

### 3.4 Willkommenstexte – **braucht neue Vorlagenarten**

* Speicher `welcome_config` hält je Art (`welcome`, `leave`) **zwei** Texte (`title` max. 256 Zeichen, `message` max. 3500) plus Kanal, Banner, Erwähnung. Die Bibliothek hat ein Titel-Feld (Name) und einen Text.
* Platzhalter sind acht andere (`user`, `display_name`, `username`, `user_id`, `server_name`, `member_count`, `account_created`, `joined_at`); keine der vorhandenen Bibliotheksarten passt, und `render_template`/Mention-Schutz aus `pulse_embed_design` wird dort bereits genutzt.
* Rechte (geprüft): Bearbeiten über `/admin/discord/welcome` nur für Administratoren (`admin=True`), Policy `admin.area`; Leser ist der Mitglieds-Beitrittsfluss ohne Person und ohne Rechtekontext.
* Nötig wären zwei neue Arten (`welcome_message`, `leave_message`) mit eigenen Platzhaltern, Policy `admin.area` und eine Abbildung von Titel+Text (zwei Vorlagen oder strukturierter Text). Ohne Nutzen für Versionierung wäre der Aufwand nur dann gerechtfertigt, wenn der Editor Versionen/Archiv anzeigt. Entscheidung offen.

## 4. Tests (`tests/test_ticket_reply_templates.py`, `tests/test_pulse_templates.py`)

Neue Tests: Bibliotheksvorlage mit Version und Platzhaltern, Bereichslogik, fehlende Vorlage, archivierte Vorlage (nicht angeboten, nicht aus Legacy wiederbelebt), fremder Server, fehlende Berechtigung (auch Legacy), Mention-Schutz in Text und Werten, ungültige Platzhalter, alte/beschädigte Datenformate des Legacy-Speichers, Fallback bei kaputter Bibliothek, kontrollierter Renderfehler, Obergrenze 25, Abgleich (Anlegen, Version, Archiv, unverändert), Übernahme durch Alt-Text bei ungültigem Platzhalter, konkurrierende Änderung, Rechte des Abgleichs, `/antwort` und Autocomplete mit Fake-Interaktion (Text, Erwähnungen, Ereignis, Ablehnungen ohne Ereignis), Speicherroute (Admin spiegelt, Teammitglied 403 ohne Bibliothekseintrag, Bibliotheksfehler blockiert nicht), `lookup` ohne Kappe. Bestehende Tests wurden nicht verändert oder abgeschwächt.
Fünf Mutationsproben (Archiv-Wiederbelebung, Rechteprüfung, Rendering, Überdeckung, Übernahme-Marker) schlagen jeweils an.

## 5. Konflikte mit anderen Worktrees

| Worktree | Überschneidung mit F8 |
|---|---|
| `Pulse-99` (#99) | `webserver.py` (anderer Bereich; F8 ändert 3 Zeilen in `/tickets/tools/replies`), `pulse_app_comms.py` (F8 nicht berührt). Bewerber-Anbindung wartet. |
| `Pulse-177-b4` | `webserver.py` (2 Zeilen), `pulse_db.py`; F8 ändert `pulse_db.py` nicht. |
| `Pulse-177-b5` | `cogs/pulse_pro.py`, `pulse_pro.py`, `pulse_command_help.py`; F8 berührt sie nicht. F8 ändert `cogs/ticket_tools.py`; b5 fasst es nicht an. |

## 6. Kriterien für „#123 Fertig“

1. Ticket-Schnellantworten: umgesetzt und getestet (dieser Bericht).
2. Bewerber-Nachrichten: nach Merge von #99 anbinden und testen.
3. Ankündigungen: Anforderungen klären, dann Funktion bauen (heute kein Bestand).
4. Moderationsgründe: Entscheidung (eigener Speicher bleibt oder lesender Spiegel).
5. Willkommenstexte: Entscheidung über zwei neue Arten und Nutzen.
6. Bearbeitungsoberfläche für Versionen/Archiv der Bibliothek.
Bis dahin bleibt #123 **Teilweise**.
