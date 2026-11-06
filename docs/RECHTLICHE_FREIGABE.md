# Rechtliche Freigabe der öffentlichen Website

**Wichtiger Hinweis:** Die Seiten sind technische Informationsentwürfe auf Grundlage des aktuellen Codes. Sie sind keine Rechtsberatung und wurden nicht durch einen Rechtsanwalt oder Datenschutzbeauftragten freigegeben. Der Betreiber muss die tatsächlichen Verantwortlichen, Datenflüsse, Rechtsgrundlagen, Speicherdauern und Plattformbedingungen prüfen.

Solange Betreiberangaben oder Freigaben fehlen, bleiben öffentliche Seiten auf `noindex, nofollow`; `robots.txt` sperrt die Indexierung und `sitemap.xml` ist nicht abrufbar.

## Pflichtangaben in der privaten VPS-`.env`

| Variable | Inhalt | Pflicht |
|---|---|---|
| `PULSE_LEGAL_NAME` | Vollständiger Name oder Organisation des Anbieters/Verantwortlichen | ja |
| `PULSE_LEGAL_ADDRESS` | Zustellfähige Anschrift; einzelne Zeilen mit `|` trennen | ja |
| `PULSE_LEGAL_EMAIL` | Erreichbare Kontakt-E-Mail | ja |
| `PULSE_LEGAL_HOSTER` | Name und Sitz des Hosting-Anbieters | ja |
| `PULSE_LEGAL_PHONE` | Telefonnummer, falls angeboten/erforderlich | nach Prüfung |
| `PULSE_LEGAL_CONTENT_RESPONSIBLE` | Inhaltlich verantwortliche Person, falls erforderlich | nach Prüfung |
| `PULSE_LEGAL_EXTRA` | Register-/USt-ID-/weitere Angaben, falls vorhanden/erforderlich | nach Prüfung |

Keine echten Namen, Anschriften oder Kontaktangaben in öffentliche GitHub-Dateien, Issues, Commits oder Chatnachrichten eintragen. Die Angaben ausschließlich auf dem VPS in `.env` pflegen.

## Manuelle Veröffentlichungsfreigabe

Die Website wird erst für Suchmaschinen freigegeben, wenn alle vier Pflichtangaben vorhanden sind und zusätzlich alle Schalter bewusst gesetzt wurden:

| Variable | Wert | Bedeutung |
|---|---|---|
| `PULSE_LEGAL_REVIEWED` | `1` | Betreiber hat Impressum, Datenschutz, tatsächliche Codeverarbeitung, Datenflüsse, Fristen und Rechtsgrundlagen geprüft |
| `PULSE_TERMS_APPROVED` | `1` | Betreiber hat die Community-/Nutzungsbedingungen ausdrücklich freigegeben |
| `PULSE_TERMS_EFFECTIVE_DATE` | `YYYY-MM-DD` | Tatsächliches Gültigkeitsdatum der freigegebenen Bedingungen |

Die Software kann weder die Rechtmäßigkeit prüfen noch die inhaltliche Richtigkeit dieser Freigabe verifizieren. Die Schalter sind eine bewusste Betreiberbestätigung, keine automatische Zertifizierung und kein Ersatz für Rechtsberatung. Solange ein Punkt fehlt, bleibt die Website gesperrt.

## Noch fachlich zu prüfen

1. **Anbieterkennzeichnung:** Anwendbarkeit und Umfang von § 5 DDG und § 18 MStV, Betreiberidentität, Verantwortlichkeit, Erreichbarkeit und ggf. weitere Pflichtangaben.
2. **Datenschutzverantwortung:** Wer entscheidet über Zwecke und Mittel der Verarbeitung? Ist ein Datenschutzbeauftragter zu benennen?
3. **Hosting:** Auftragsverarbeitungsvertrag nach Art. 28 DSGVO, Rechenzentrumsort, Unterauftragnehmer und Backups.
4. **Discord/Roblox:** Rollenverteilung, Datenübermittlungen, Plattformbedingungen und mögliche Drittlandtransfers.
5. **KI-Anbieter:** Vor Aktivierung Auftragsverarbeitung, Drittlandtransfer, Nutzung zum Training und Speicherzeiten des konkreten Tarifs prüfen. KI-Moderation und Support müssen transparent beschrieben sein.
6. **Minderjährige:** Verständlichkeit der Texte, Altersvorgaben und Einwilligungsfragen prüfen.
7. **Speicherfristen:** Aktuelle technische Lücken nicht durch Versprechen verdecken:
   - Die automatische Bereinigung von Bewerbungen ist aktuell deaktiviert; die dokumentierte Konfiguration ist keine ausgeführte Löschung.
   - Ticket-Transkripte werden derzeit ohne automatische Frist aufbewahrt.
   - Teamdaten und Moderationsfälle benötigen klare, technisch durchgesetzte Fristen.
8. **Rechtsgrundlagen:** Insbesondere Art. 6 Abs. 1 lit. f DSGVO braucht eine dokumentierte Interessenabwägung.
9. **Rechte der Betroffenen:** Verantwortlichkeit, Kontaktweg, Fristen und Ablauf für Auskunft, Berichtigung, Löschung und Widerspruch organisatorisch festlegen.
10. **Regelwerk und Marken:** Veröffentlichung mit der Serverleitung abstimmen und fremde Marken-/Organisationsnennungen prüfen.
11. **Cookies/TDDDG:** Jede Speicherung oder jeder Zugriff im Browser einzeln bewerten, auch lokale Präferenzen im Dashboard.
12. **Gesetzesstand:** DDG, TDDDG, DSGVO und MStV vor Veröffentlichung anhand amtlicher aktueller Fassungen prüfen.

## Cookie- und Consent-Verhalten

Der öffentliche Auftritt lädt aktuell keine Analyse-, Marketing- oder Drittanbieter-Einbettungsdienste. Die öffentlichen Seiten setzen beim bloßen Aufruf keine Cookies oder Local-/Session-Storage-Einträge. Deshalb zeigt die Website keinen Einwilligungsdialog, der eine nicht vorhandene Verarbeitung vortäuschen würde.

Die Cookie-Seite dokumentiert die Cookies und Browser-Speicher, die in Login, Bewerbung und Dashboard verwendet werden. Bei jeder späteren Integration optionaler Dienste müssen:
- Dienst, Anbieter, Zweck und Datenfluss vorab dokumentiert werden;
- sämtliche optionalen Anfragen standardmäßig blockiert bleiben;
- „Alle akzeptieren“, „Nur notwendige“ und eine individuelle Auswahl gleichwertig zugänglich sein;
- alle Optionen standardmäßig deaktiviert sein;
- Widerruf/Änderung jederzeit erreichbar sein;
- Datenschutzerklärung, Cookie-Tabelle, Consent-Version, CSP und Tests gemeinsam angepasst werden.

## Öffentliche Teamprofile

Teamprofile werden nicht aus Discord oder der internen Teamliste synchronisiert. Administratoren legen eine öffentliche Kurzfassung manuell an. Sie startet immer als Entwurf; erst die ausdrückliche Veröffentlichung macht Anzeigename, Funktion und Bio sichtbar. Vor Veröffentlichung muss die betroffene Person zugestimmt haben. Private IDs, Notizen, Verwarnungen, Dienstzeiten, Kontaktdaten und Rechte bleiben intern.

