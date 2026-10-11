# Pulse KI-Support für Discord

Der Support-Assistent reagiert ausschließlich in Channel `1493649456086782083` (oder auf die
explizit konfigurierte `PULSE_SUPPORT_CHANNEL_ID`). Er antwortet auf erkennbare Fragen,
Hilferufe und Nachrichten, die Pulse direkt erwähnen oder auf eine Pulse-Antwort reagieren.
Normale Unterhaltungen lösen keine Antwort aus.

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
```

3. Entferne oder ignoriere für diese Konfiguration die alten OpenAI-Einstellungen. Achte darauf, dass für den Gemini-Schlüssel kein kostenpflichtiges API-Projekt verwendet wird, wenn du ausschließlich das kostenlose Kontingent nutzen möchtest.
4. Starte Pulse neu und führe in Discord als Administrator `/supportbot_status` und danach `/supportbot_test` aus.

Der Code verwendet für OpenAI und Gemini denselben kompatiblen Chat-Completions-Aufruf. Ein ChatGPT-Abonnement ist kein OpenAI-API-Guthaben. Das Gemini-Free-Tier bleibt an Googles aktuelle Quoten und Bedingungen gebunden; bei ausgeschöpfter Quote muss die Grenze zurückgesetzt werden, statt auf bezahlte Nutzung umzustellen.

## Alternative: OpenAI API

Wenn du ausdrücklich die OpenAI-API verwenden möchtest, setze `PULSE_SUPPORT_PROVIDER=openai` und konfiguriere `OPENAI_API_KEY`, optional `OPENAI_MODEL` (Standard `gpt-4.1-mini`) und `OPENAI_BASE_URL` (Standard `https://api.openai.com/v1`). Die OpenAI-API wird separat abgerechnet und benötigt ein verfügbares API-Guthaben.

`PULSE_SUPPORT_CHANNEL_ID` ist optional; Standard ist `1493649456086782083`. Solange `PULSE_SUPPORT_ENABLED` nicht auf `true` steht oder der zum Anbieter passende API-Schlüssel fehlt, beantwortet die KI keine Nachrichten. `/supportbot_status` zeigt die Konfiguration, `/supportbot_test` prüft die API-Verbindung und kann einen kleinen API-Aufruf auslösen.

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
