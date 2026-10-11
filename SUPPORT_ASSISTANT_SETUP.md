# Pulse KI-Support für Discord

Der Support-Assistent reagiert ausschließlich in Channel `1493649456086782083` (oder auf die
explizit konfigurierte `PULSE_SUPPORT_CHANNEL_ID`). Er antwortet auf erkennbare Fragen,
Hilferufe und Nachrichten, die Pulse direkt erwähnen oder auf eine Pulse-Antwort reagieren.
Normale Unterhaltungen lösen keine Antwort aus.

## Hosting konfigurieren

Lege folgende Umgebungsvariablen im Hosting-Panel an:

- `OPENAI_API_KEY`: API-Schlüssel aus dem OpenAI-API-Projekt. Niemals in GitHub, Chat oder öffentliche
  Discord-Nachrichten schreiben.
- `OPENAI_MODEL`: optional, Standard ist `gpt-4.1-mini`.
- `OPENAI_BASE_URL`: optional, Standard ist `https://api.openai.com/v1`.
- `PULSE_SUPPORT_CHANNEL_ID`: optional, Standard ist `1493649456086782083`.

Die API-Nutzung wird separat nach dem API-Tarif abgerechnet; ein ChatGPT-Abonnement ist nicht
automatisch ein API-Guthaben. Ohne `OPENAI_API_KEY` antwortet die KI nicht. Der Slash-Befehl
`/supportbot_status` zeigt die Konfiguration, `/supportbot_test` prüft die API-Verbindung und
kann dabei einen kleinen API-Aufruf auslösen.

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
- Er darf keine internen Teamdaten, Geheimnisse, API-Schlüssel oder nicht veröffentlichte
  Einstellungen ausgeben.
- Bei Sanktionen, Beschwerden, Bewerbungsentscheidungen und anderen verbindlichen Fragen verweist
  er an das Team.
- Antworten sind begrenzt; es gibt Cooldowns pro Person und einen globalen Cooldown gegen Spam und
  unerwartete API-Kosten.
- Er durchsucht das Internet nicht automatisch und kann ohne gepflegte Wissensbasis keine aktuellen
  oder internen Serverinformationen kennen.
