# Pulse KI-Support für Discord

Der Support-Assistent antwortet im öffentlichen Support-Channel `1493649456086782083` (oder der
konfigurierten `PULSE_SUPPORT_CHANNEL_ID`). Optional kann ein separater privater Team-Hilfekanal
über `PULSE_SUPPORT_TEAM_CHANNEL_ID` aktiviert werden. Dort hilft Pulse unter anderem beim
Formulieren von Ticketantworten, Reports, Ankündigungen, Checklisten und Trainingsmaterial.
Normale Unterhaltungen lösen keine Antwort aus; Fragen und klare Arbeitsaufträge werden erkannt.
Für allgemeine Themen nutzt der Assistent sein allgemeines Wissen. Konkrete Bochum-RP-Regeln,
Rollen, Fristen und Serverabläufe darf er nur aus `support_knowledge.md` ableiten.

## Kostenlose Variante mit Google Gemini

Du kannst den Assistenten ohne OpenAI-API-Guthaben mit dem kostenlosen Kontingent der Gemini Developer API betreiben. Das kostenlose Kontingent hat Nutzungsgrenzen und die Verfügbarkeit der Modelle kann sich ändern. Google weist darauf hin, dass Inhalte im Free-Tier zur Verbesserung seiner Produkte verwendet werden können.

1. Erstelle in [Google AI Studio](https://aistudio.google.com/apikey) einen Gemini-API-Schlüssel. Gib ihn niemals in GitHub, Chat oder Discord weiter.
2. Öffne die Umgebungsvariablen im Hosting-Panel und lege diese Werte an:

```env
PULSE_SUPPORT_PROVIDER=gemini
PULSE_SUPPORT_ENABLED=true
GEMINI_API_KEY=DEIN_GEMINI_API_SCHLUESSEL
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
PULSE_SUPPORT_CHANNEL_ID=1493649456086782083
# Optionaler Team-Hilfekanal: ID des privaten Team-Channels eintragen
PULSE_SUPPORT_TEAM_CHANNEL_ID=DEINE_TEAM_CHANNEL_ID
# Optional: Rollen, die im Team-Channel die KI nutzen dürfen (kommasepariert)
PULSE_SUPPORT_TEAM_ROLE_IDS=TEAM_ROLLEN_ID_1,TEAM_ROLLEN_ID_2
# Umfangreicheres Antwortbudget (300–1200; Standard 750)
PULSE_SUPPORT_MAX_TOKENS=750
```

3. Entferne oder ignoriere für diese Konfiguration die alten OpenAI-Einstellungen. Achte darauf, dass für den Gemini-Schlüssel kein kostenpflichtiges API-Projekt verwendet wird, wenn du ausschließlich das kostenlose Kontingent nutzen möchtest.
4. Starte Pulse neu und führe in Discord als Administrator `/supportbot_status` und danach `/supportbot_test` aus.

Der Code verwendet für OpenAI und Gemini denselben kompatiblen Chat-Completions-Aufruf. Ein ChatGPT-Abonnement ist kein OpenAI-API-Guthaben. Das Gemini-Free-Tier bleibt an Googles aktuelle Quoten und Bedingungen gebunden; bei ausgeschöpfter Quote muss die Grenze zurückgesetzt werden, statt auf bezahlte Nutzung umzustellen.

## Alternative: OpenAI API

Wenn du ausdrücklich die OpenAI-API verwenden möchtest, setze `PULSE_SUPPORT_PROVIDER=openai` und konfiguriere `OPENAI_API_KEY`, optional `OPENAI_MODEL` (Standard `gpt-4.1-mini`) und `OPENAI_BASE_URL` (Standard `https://api.openai.com/v1`). Die OpenAI-API wird separat abgerechnet und benötigt ein verfügbares API-Guthaben.

`PULSE_SUPPORT_CHANNEL_ID` ist optional; Standard ist `1493649456086782083`. `PULSE_SUPPORT_TEAM_CHANNEL_ID` ist optional; nur mit einer echten Channel-ID wird der zusätzliche Team-Hilfekanal aktiv. Nutze einen privaten Channel und trage bei Bedarf die erlaubten Team-Rollen in `PULSE_SUPPORT_TEAM_ROLE_IDS` ein. `PULSE_SUPPORT_MAX_TOKENS` kann den Antwortumfang zwischen 300 und 1200 Tokens einstellen (Standard 750). Solange `PULSE_SUPPORT_ENABLED` nicht auf `true` steht oder der zum Anbieter passende API-Schlüssel fehlt, beantwortet die KI keine Nachrichten. `/supportbot_status` zeigt die Konfiguration, `/supportbot_test` prüft die API-Verbindung und kann einen kleinen API-Aufruf auslösen.

## Serverwissen pflegen

Bearbeite `support_knowledge.md` und ergänze dort ausschließlich bestätigte Serverregeln, FAQs,
Bewerbungsvoraussetzungen und offizielle Links. Ohne diese Faktenbasis darf der Assistent keine
Bochum-RP-Regeln, Teamzuständigkeiten oder Entscheidungen erfinden. Die Datei wird für jede
Antwort neu gelesen, Änderungen werden daher nach dem Speichern verwendet.

## Discord-Berechtigungen

Der Bot braucht im genannten Channel mindestens `Kanal ansehen`, `Nachrichten senden` und
`Nachrichtenverlauf lesen`. In den Discord Developer Portal Einstellungen muss außerdem der
privilegierte **Message Content Intent** aktiviert sein; `main.py` fordert ihn bereits an.

## Datenschutz-Hinweis vor dem Einsatz

Für eine KI-Antwort wird der Inhalt der jeweiligen Frage zusammen mit einem begrenzten Verlauf
derselben Person an den konfigurierten KI-API-Anbieter übertragen. Bitte informiere Mitglieder
sichtbar im Channel, bevor du die Funktion freischaltest. Beispiel:

> 🤖 **KI-Support von Pulse:** In diesem Channel kann Pulse Fragen automatisch beantworten.
> Zur Erstellung der Antwort werden deine Nachricht und ein kurzer Verlauf deiner eigenen
> Unterhaltung an den konfigurierten KI-Anbieter übermittelt. Bitte poste keine Passwörter,
> Zugangsdaten oder unnötigen personenbezogenen Daten. KI-Antworten können Fehler enthalten;
> verbindliche Entscheidungen trifft ausschließlich das Team.

## Grenzen und Schutzmaßnahmen

- Der Assistent löscht keine Nachrichten, vergibt keine Rollen, erteilt keine Warnungen und kickt
  niemanden. Er ist ein Frage-/Antwort-Assistent, keine automatische Sanktionsmoderation.
- Allgemeine Fragen und Arbeitsaufträge (z. B. Textentwürfe, Checklisten, Erklärungen oder
  Trainingsideen) können beantwortet werden. Serverregeln und interne Abläufe bleiben an die
  bestätigte Wissensbasis gebunden.
- Der Gesprächsverlauf wird pro Person und Channel getrennt gehalten, damit ein Gespräch im
  öffentlichen Support nicht als Kontext im privaten Teamkanal wiederverwendet wird.
- Er darf keine internen Teamdaten, Geheimnisse, API-Schlüssel oder nicht veröffentlichte
  Einstellungen ausgeben.
- Bei Sanktionen, Beschwerden, Bewerbungsentscheidungen und anderen verbindlichen Fragen verweist
  er an das Team.
- Antworten sind begrenzt; es gibt Cooldowns pro Person und einen globalen Cooldown gegen Spam und
  unerwartete API-Kosten.
- Er durchsucht das Internet nicht automatisch und kann ohne gepflegte Wissensbasis keine aktuellen
  oder internen Serverinformationen kennen.


## KI-Nachrichtenmoderation (optional)

Pulse kann in der konfigurierten Discord-Guild eingehende **Textnachrichten in allen nicht ausgeschlossenen Textkanälen** mit demselben Gemini-/OpenAI-kompatiblen KI-Anbieter auf mögliche Verstöße gegen die Bochum-RP-Regeln prüfen. Nur Verdachtsfälle ab der eingestellten Konfidenz werden in einen privaten Review-Kanal gemeldet. Bilder/Dateien ohne Text sowie Voice-Audio werden nicht analysiert.

### Aktivierung im Hosting

Erstelle zuerst einen **privaten Moderations-Prüfkanal** für berechtigte Teammitglieder. Gib Pulse dort mindestens Kanal ansehen, Nachrichten senden und Nachrichtenverlauf lesen. Verwende nicht den öffentlichen Support-Channel für interne Verdachtsmeldungen.

Lege danach folgende Variablen im Hosting-Panel an:

```env
PULSE_AI_MODERATION_ENABLED=true
PULSE_AI_MODERATION_REVIEW_CHANNEL_ID=ID_DEINES_PRIVATEN_PRUEFKANALS
PULSE_AI_MODERATION_INTERVAL_SECONDS=4
PULSE_AI_MODERATION_CONFIDENCE=0.72
PULSE_AI_MODERATION_EXCLUDED_CHANNEL_IDS=
PULSE_AI_MODERATION_EXCLUDED_CATEGORY_IDS=
```

Die Moderation verwendet dieselbe Anbieter-Konfiguration wie der Support-Assistent: `PULSE_SUPPORT_PROVIDER`, `GEMINI_API_KEY` und `GEMINI_MODEL` für Gemini oder die entsprechenden `OPENAI_...`-Variablen. Die Bürgerrollen sind im Code als `1531303733311307997` und `1531459374004834424` hinterlegt. Teamrollen werden aus den Pulse-Einstellungen gelesen. Mit `PULSE_AI_MODERATION_EXCLUDED_CHANNEL_IDS` und `PULSE_AI_MODERATION_EXCLUDED_CATEGORY_IDS` kannst du private Ticket-Channels oder ganze Ticket-Kategorien von der KI-Übermittlung ausschließen (mehrere IDs kommasepariert).

Starte den Bot neu und führe als Discord-Administrator `/aimod_status` aus. Der Status zeigt, ob die Moderation aktiviert ist, ob der Prüfkanal gefunden wurde und ob Nachrichten auf die Warteschlange warten.

### Ablauf und Sanktionen

- Die KI liest den Text, ordnet mögliche Regeln zu und erstellt einen Verdachtsbericht mit Beleg-Link, Regelhinweis, Schweregrad und Konfidenz.
- **Die KI führt niemals allein eine Sanktion aus.** Berechtigte Teammitglieder müssen den Fall im privaten Prüfkanal bestätigen oder ablehnen.
- Bei Bürgern können berechtigte Prüfer eine Verwarnung per DM, einen Timeout von 10 Minuten oder 1 Stunde, Kick oder Ban auswählen. Discord-Berechtigungen und Rollen-Hierarchie gelten weiterhin.
- Bei Teammitgliedern kann nach menschlicher Prüfung eine Team-Warnung über das bestehende Pulse-Warnsystem eingetragen werden.
- Achtung: Das bestehende Team-Warnsystem hat automatische Folgen: ab Warnstufe 2 Uprank-Sperre, ab Stufe 3 IC-Rechte-Sperre, ab Stufe 4 Downrank, ab Stufe 5 Entfernung der Teamrollen. Der Prüfbericht zeigt die voraussichtliche Folge vor der Bestätigung.
- Nachrichten von Bots/Webhooks sowie Nachrichten ohne Text werden übersprungen. Bei API-Limits pausiert die Prüfung; bei voller Warteschlange können Nachrichten ausgelassen werden. Eine KI-Erkennung ist nie fehlerfrei, deshalb ist die menschliche Prüfung erforderlich.

### Datenschutz und Transparenz

Die Textnachrichten werden zur Klassifizierung an den konfigurierten KI-Anbieter übertragen. Das umfasst bei aktivierter Prüfung auch Nachrichten in privaten Ticketkanälen. Informiere Mitglieder vor der Aktivierung darüber und prüfe, ob private Supportkanäle aus Datenschutzgründen ausgeschlossen werden sollen. Beim kostenlosen Gemini-Tarif gelten Googles jeweilige Datenverwendungsbedingungen; poste keine Passwörter oder sonstigen Geheimnisse. Moderationsfälle werden lokal in `ai_moderation_cases.json` zwischengespeichert; erledigte Fälle werden nach 30 Tagen entfernt, offene Fälle bleiben gespeichert, bis sie bearbeitet werden.
