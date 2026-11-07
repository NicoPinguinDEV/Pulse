# Rechtliche Freigabe der öffentlichen Website

**Wichtiger Hinweis:** Die öffentlichen Rechtstexte sind technische Entwürfe auf Grundlage des Repository-Codes. Sie sind keine Rechtsberatung, keine anwaltlich geprüften AGB und keine Bestätigung der DSGVO-Konformität. Die Software kann weder die tatsächliche Betreiberstruktur noch Verträge, Interessenabwägungen, Einwilligungen oder die rechtliche Wirksamkeit einer Klausel überprüfen.

Die Veröffentlichung bleibt durch die bestehenden Freigabeschalter geschützt. Solange Angaben oder Freigaben fehlen, erhalten Seiten `noindex, nofollow`, `robots.txt` sperrt Suchmaschinen aus und `sitemap.xml` ist nicht abrufbar. **Das ist keine Zugangssperre:** Die URLs bleiben öffentlich erreichbar und gesetzliche Informationspflichten werden dadurch nicht aufgehoben.

## 1. Private Betreiberangaben auf dem VPS
## 1. Betreiber ist eine natürliche Person (Privatperson)

Der Betreiber von BOCHUM RP ist nach der vom Betreiber mitgeteilten Organisationsform eine **natürliche Person**, kein eingetragener Verein und keine Gesellschaft. Deshalb gilt für die rechtliche Konfiguration:

- `PULSE_LEGAL_NAME`: vollständiger bürgerlicher Vor- und Nachname des tatsächlichen Anbieters. Nicht nur „BOCHUM RP“, „Pulse“, ein Discord-Name oder ein Gaming-Pseudonym.
- `PULSE_LEGAL_ADDRESS`: vollständige zustellfähige Anschrift des Anbieters (Straße, Hausnummer, Postleitzahl und Ort), soweit nach der konkreten Anwendbarkeit der Informationspflichten erforderlich.
- `PULSE_LEGAL_FORM`: bei einer natürlichen Person nicht mit „Firma“, „Verein“ oder einer erfundenen Rechtsform befüllen.
- `PULSE_LEGAL_REPRESENTATIVE`: bei einer natürlichen Person als Anbieter grundsätzlich nicht als Organ-/Vertreterfeld ausfüllen. Es ist keine Firma oder ein Verein zu erfinden, um die Privatperson zu verschleiern.
- Register-, Kapital- und Umsatzsteuerangaben sind nur anzugeben, wenn sie für diese Person tatsächlich bestehen bzw. gesetzlich einschlägig sind. Keine Platzhalter und keine erfundenen Nummern veröffentlichen.

Die öffentliche BOCHUM-RP-Website richtet sich an eine Community und mögliche Bewerber und ist nicht lediglich für Familie oder persönliche Freunde bestimmt. Daher ist nach dem Wortlaut von § 18 Abs. 1 MStV grundsätzlich von einer Anbieterkennzeichnung mit Vor-/Nachname und Anschrift auszugehen. Die genaue rechtliche Einordnung und die Anschrift müssen vor Veröffentlichung geprüft werden.

**Privatsphäre:** Eine c/o-Anschrift oder ein Zustellungsbevollmächtigter kann unter Umständen eine Alternative zur privaten Wohnanschrift sein. Das ist keine automatische Freistellung: Die dort genannte Person muss ausdrücklich berechtigt sein, rechtserhebliche Post für den Anbieter entgegenzunehmen und unverzüglich weiterzuleiten; die Anschrift muss für tatsächliche Zustellungen geeignet sein. Ein bloßes Postfach, eine Packstation oder eine virtuelle Briefkasten-/Weiterleitungsadresse genügt nicht ohne Weiteres. Vor Nutzung einer solchen Lösung sollte die Empfangsvollmacht schriftlich vereinbart und die konkrete Konstruktion anwaltlich oder durch eine zuständige Medienanstalt geprüft werden. [Medienanstalt Hessen: Impressumspflicht](https://www.medienanstalt-hessen.de/aufsicht/telemedien-digitale-dienste-internetaufsicht/impressumspflicht/).


Echte Namen, Anschriften und private Kontaktdaten gehören nicht in öffentliche GitHub-Dateien, Issues, Commits oder Chatnachrichten. Die Werte werden ausschließlich in der privaten VPS-Umgebung gepflegt.

| Variable | Inhalt | Einordnung |
|---|---|---|
| `PULSE_LEGAL_NAME` | Vollständiger Name oder Organisation des Anbieters | erforderlich für die Freigabe |
| `PULSE_LEGAL_ADDRESS` | Zustellfähige Anschrift; Zeilen mit `|` trennen | erforderlich für die Freigabe |
| `PULSE_LEGAL_EMAIL` | Erreichbare Kontakt-E-Mail | erforderlich für die Freigabe |
| `PULSE_LEGAL_HOSTER` | Name und Sitz des Hosting-Anbieters | erforderlich für die Freigabe |
| `PULSE_LEGAL_PHONE` | Telefonnummer | optional, soweit vorhanden/erforderlich |
| `PULSE_LEGAL_FORM` | Rechtsform | falls eine Organisation/juristische Person vorliegt |
| `PULSE_LEGAL_REPRESENTATIVE` | Name und Anschrift der vertretungsberechtigten Person | falls rechtlich erforderlich |
| `PULSE_LEGAL_REGISTER` | Registergericht und Registernummer | falls eingetragen |
| `PULSE_LEGAL_VAT_ID` | Umsatzsteuer-Identifikationsnummer | falls vorhanden |
| `PULSE_LEGAL_WIDNR` | Wirtschafts-Identifikationsnummer | falls vorhanden |
| `PULSE_LEGAL_AUTHORITY` | Zuständige Aufsichtsbehörde | nur falls die Tätigkeit einer behördlichen Zulassung unterliegt |
| `PULSE_LEGAL_PROFESSION` | Kammer, Berufsbezeichnung, Staat und Berufsrecht | nur falls eine reglementierte berufliche Tätigkeit ausgeübt wird |
| `PULSE_LEGAL_CONTENT_RESPONSIBLE` | Vollständiger Name und Anschrift der inhaltlich verantwortlichen Person | nur falls § 18 Abs. 2 MStV einschlägig ist |
| `PULSE_LEGAL_EXTRA` | Zusätzliche Angaben | nur falls tatsächlich erforderlich |

Da BOCHUM RP nach der vorliegenden Angabe von einer natürlichen Person betrieben wird, sind die Felder für Rechtsform und vertretungsberechtigte Person derzeit nicht als Firmen-/Vereinsangaben auszufüllen. Sollte sich die Organisationsform später ändern, müssen Impressum und Datenschutz vor der Änderung erneut geprüft werden.

### Impressum: Anwendbarkeit richtig einordnen

- **§ 18 Abs. 1 MStV** erfasst Telemedien, die nicht ausschließlich persönlichen oder familiären Zwecken dienen. Er verlangt leicht erkennbare, unmittelbar erreichbare und ständig verfügbare Angaben zu Name und Anschrift sowie bei juristischen Personen zusätzlich Name und Anschrift der vertretungsberechtigten Person.
- **§ 5 DDG** kann zusätzlich gelten, wenn der digitale Dienst geschäftsmäßig und in der Regel gegen Entgelt angeboten wird. Das kann auch bei einer natürlichen Person zutreffen; eine Firma oder Gewinnerzielungsabsicht ist nicht zwingend die entscheidende Frage. Zu prüfen sind insbesondere Werbung, Sponsoring, bezahlte Leistungen/Zugänge, regelmäßige entgeltliche Angebote oder andere wirtschaftliche Gegenleistungen. Wenn § 5 DDG gilt, benötigt das Impressum neben Name und Anschrift eine E-Mail-Adresse und eine weitere Möglichkeit für schnelle elektronische Kontaktaufnahme und unmittelbare Kommunikation (z. B. Telefon oder ein tatsächlich funktionierendes Kontaktformular). Register-, Berufs- und Aufsichtsangaben sind nur aufzunehmen, soweit sie im konkreten Fall einschlägig sind.
- **§ 18 Abs. 2 MStV** ist gesondert zu prüfen, weil die Website auch einen News-/Blog-Bereich hat. Die Norm betrifft journalistisch-redaktionell gestaltete Angebote; die Einordnung hängt von Inhalt, redaktioneller Organisation und tatsächlicher Ausgestaltung ab. Falls sie greift, muss eine verantwortliche Person mit Vor- und Nachnamen sowie Anschrift benannt werden. Die gesetzlichen persönlichen Voraussetzungen sind zu beachten. Es darf nicht automatisch angenommen werden, dass der Betreiber auch diese Funktion übernehmen darf.

Die Seiten sollen die Normen deshalb nicht pauschal so darstellen, als seien sämtliche Zusatzangaben für jedes Hobbyprojekt automatisch erforderlich. Die konkrete Anbieterstruktur und die tatsächlichen Inhalte müssen rechtlich eingeordnet werden.

Eine c/o- oder Empfangsadresse darf nicht einfach als Briefkastenersatz verwendet werden. Vor ihrer Nutzung muss geklärt sein, dass dort rechtserhebliche Zustellungen tatsächlich und zuverlässig entgegengenommen werden dürfen. Eine Postfach-/Packstationsadresse oder reine virtuelle Weiterleitung ist nicht ohne Weiteres eine zustellfähige Anschrift. Eine zulässige Empfangsbevollmächtigung ist eine individuelle Rechtsfrage; die private Wohnanschrift darf nicht durch erfundene Angaben ersetzt werden.

## 2. Verantwortlicher und Datenschutzkontakt

Standardmäßig verwendet die Datenschutzerklärung die Anbieterangaben aus `PULSE_LEGAL_*`. Nur wenn der datenschutzrechtlich Verantwortliche tatsächlich eine andere Person/Organisation ist, können die folgenden privaten VPS-Werte abweichen:

| Variable | Inhalt |
|---|---|
| `PULSE_PRIVACY_CONTROLLER_NAME` | Abweichender Name des Verantwortlichen |
| `PULSE_PRIVACY_CONTROLLER_ADDRESS` | Abweichende Anschrift des Verantwortlichen; Zeilen mit `|` trennen |
| `PULSE_PRIVACY_CONTROLLER_EMAIL` | Abweichende Kontakt-E-Mail |
| `PULSE_PRIVACY_DPO_CONTACT` | Kontaktdaten eines Datenschutzbeauftragten, falls einer benannt werden muss |
| `PULSE_PRIVACY_SUPERVISORY_AUTHORITY` | Konkrete zuständige Datenschutzaufsichtsbehörde |
| `PULSE_PRIVACY_TRANSFER_INFO` | Verifizierte Angaben zu Drittland, Empfängerrolle und Übermittlungsmechanismus |

Die Identität des Verantwortlichen darf nicht mit dem Hosting-Anbieter, Discord oder dem Betreiber einer KI-API verwechselt werden. Ob ein Datenschutzbeauftragter bestellt werden muss, ist anhand der gesetzlichen Kriterien und der tatsächlichen Verarbeitung zu prüfen. Die zuständige Aufsichtsbehörde richtet sich nach dem Verantwortlichen und den gesetzlichen Zuständigkeitsregeln, nicht automatisch nach dem Wohnort eines Website-Besuchers.

## 3. Manuelle Freigabe bleibt gesperrt

Eine Suchmaschinenfreigabe ist nur möglich, wenn alle vier Pflichtangaben vorhanden sind und die Betreiberfreigaben bewusst gesetzt wurden:

| Variable | Wert | Bedeutung |
|---|---|---|
| `PULSE_LEGAL_REVIEWED` | `1` | Verantwortlicher hat Anbieterangaben, Datenflüsse, Empfänger, Speicherpraxis, Rechtsgrundlagen, Betroffenenrechte und konkrete Verträge geprüft |
| `PULSE_TERMS_APPROVED` | `1` | Verantwortlicher hat die Nutzungsbedingungen ausdrücklich geprüft und freigegeben |
| `PULSE_TERMS_EFFECTIVE_DATE` | `YYYY-MM-DD` | Tatsächliches Gültigkeitsdatum der freigegebenen Fassung |

Die Werte wurden in diesem PR **nicht gesetzt, nicht geändert und nicht auf dem VPS geprüft**. Auch ein gesetzter Schalter ist nur eine bewusste Betreiberbestätigung, keine automatische Rechtsprüfung und keine Zertifizierung.

## 4. Datenverarbeitung: Code-Stand und bekannte Grenzen

Die Datenschutzerklärung unterscheidet nun stärker zwischen dem, was der Code tatsächlich tut, und dem, was der Betreiber rechtlich noch belegen muss.

### Website und Team-Login

- Bei Seitenaufrufen können IP-Adresse, Zeitpunkt, URL und Browserkennung für Auslieferung, Rate-Limiting und Sicherheit verarbeitet werden.
- Der Team-Login nutzt Discord OAuth mit dem Scope `identify`; Pulse erhält Discord-ID, Namen und Avatar.
- Login-IP-Auditdaten haben im aktuellen Code eine 30-Tage-Aufbewahrung; Dashboard-Sitzungen enden nach 7 Tagen bzw. 24 Stunden Inaktivität.
- Die öffentlichen Teamprofile werden seit dem Merge von PR #164 live aus den konfigurierten Discord-Teamrollen erzeugt. Sichtbar sind Anzeigename, Teamrolle und gegebenenfalls Profilbild; Präsenzstatus, Abwesenheiten, interne Notizen, Verwarnungen und private Kontaktdaten sind nicht Teil der öffentlichen Karten.
- Bei Profilbildern fordert der Browser Ressourcen von Discord-CDN-Domains an. Eine CDN-Bildadresse kann technische Discord-Kennungen enthalten, auch wenn keine Discord-ID als sichtbares Profilfeld ausgegeben wird.

**Technische Schutzmaßnahme in diesem PR:** Die öffentliche Teamseite zeigt nur noch Personen mit einer ausdrücklich erteilten, versionsgebundenen Freigabe. Die Person muss in Discord den Befehl `/teamseite_freigabe` aufrufen, die ausführliche Information lesen und eine separate Bestätigungsschaltfläche anklicken. Die Freigabe speichert Discord-ID, Textversion, Teamrollen-Scope und Zeitstempel in `pulse.db`. Ohne Nachweis wird kein Profil ausgegeben. Widerruf ist über `/teamseite_freigabe_widerrufen` möglich; Rollenänderungen und Serveraustritt beenden die aktuelle Freigabe. Die Freigabe hat keine Auswirkung auf Teammitgliedschaft oder Berechtigungen.

**Weiterhin offen:** Der Betreiber muss sicherstellen, dass die Informationspflichten nach Art. 13 DSGVO vor der Freigabe erfüllt werden und – soweit Daten aus Discord übernommen werden – die Information nach Art. 14 DSGVO im konkreten Aufnahmeprozess erfolgt. Die tatsächliche Identität des Verantwortlichen, der Kontaktweg und eine zuständige Aufsichtsbehörde müssen korrekt sein. Bei Minderjährigen ist vor der Freigabe zu klären, ob zusätzliche Schutz- oder Zustimmungserfordernisse greifen. Die Löschfrist des minimalen Freigabenachweises (einschließlich Widerrufszeitpunkt) ist noch festzulegen und technisch umzusetzen. Minderjährige müssen vor der Freigabe gesondert beurteilt werden; die technische Schaltfläche ersetzt keine gegebenenfalls erforderliche Zustimmung von Erziehungsberechtigten.

### Bewerbungen, Teamdaten und Löschung

- Bewerbungen enthalten Identität, Antworten, Testergebnisse, Praxisaufgaben und Bearbeitungsstatus.
- Die konfigurierbaren Zielwerte der Retention-Funktion sind kein Beleg für eine ausgeführte Löschung: `RETENTION_MUTATIONS_ENABLED = False`; die zentrale Bereinigung bleibt fail-closed.
- Daher dürfen Bewerbungs- oder Auditdaten nicht als automatisch fristgerecht gelöscht beschrieben werden.
- Für Teamdaten besteht derzeit keine einheitlich technisch durchgesetzte Löschfrist für alle Teilbestände. Der Verantwortliche muss pro Datenkategorie tatsächliche Fristen und Verantwortlichkeiten festlegen.
- Ticket-Transkripte werden laut aktuellem Code ohne automatische Löschfrist aufbewahrt.
- Entschiedene KI-Moderationsfälle werden derzeit nach 30 Tagen bereinigt; offene Fälle können länger bestehen bleiben.
- Backups rotieren nach aktuellem Code täglich 7 Tage, wöchentlich 4 Wochen und monatlich 6 Monate. Eine Löschung aus dem Live-System entfernt Daten nicht sofort aus vorhandenen Sicherungen.

### KI-Unterstützung und Moderation

- Der Support-Assistent übermittelt bei aktiver Konfiguration die aktuelle Frage und bis zu acht vorherige gespeicherte Gesprächsnachrichten an den konfigurierten Anbieter (Google Gemini oder OpenAI).
- Die KI-Moderation kann Nachrichtentext, Discord-Nutzerkennung/-Name, Kanal, Rollen, Erwähnungen, Zeitstempel und technische Fallmerkmale an den KI-Anbieter übertragen.
- `/ki_vergessen` entfernt den Pulse-intern gespeicherten Gesprächsverlauf. Es löscht nicht automatisch bereits übermittelte Daten oder Protokolle beim externen Anbieter.
- Anbieter, Region, Auftragsverarbeitung, Unterauftragnehmer, Speicherfristen, Trainingsverwendung und Drittlandtransfer hängen vom konkret verwendeten Konto, Tarif und Vertrag ab. Diese Tatsachen müssen vor Aktivierung bzw. endgültiger Freigabe anhand der realen Anbieter-Unterlagen geprüft werden.

### Cookies und Browser-Speicher

Die Cookie-Seite listet Login-Cookies, OAuth-State, Bewerbungsentwürfe sowie Dashboard-Präferenzen. Die technische Verwendung allein beweist nicht automatisch, dass jeder Zugriff/Speichervorgang unter § 25 Abs. 2 TDDDG fällt. Insbesondere Präferenzen, Formularentwürfe und optionale Funktionen müssen einzeln nach Zweck und Erforderlichkeit geprüft werden. Bei späteren Analyse-, Marketing- oder Einbettungsdiensten muss die Consent-Implementierung vor der ersten optionalen Anfrage funktionieren.

## 5. Datenschutzprüfung vor Freigabe

- [ ] Den tatsächlichen privaten Betreiber mit vollständigem Vor- und Nachnamen und einer zustellfähigen Anschrift eindeutig bestimmen; keine Projektbezeichnung als Anbietername verwenden.
- [ ] Prüfen, ob für die Anschrift ein rechtlich belastbarer Zustellungsbevollmächtigter mit schriftlicher Empfangs- und Weiterleitungsvollmacht genutzt werden kann.
- [ ] Prüfen, ob § 5 DDG wegen Entgeltlichkeit/geschäftsmäßiger Ausgestaltung greift; bei Anwendbarkeit E-Mail plus weitere schnelle elektronische Kontaktmöglichkeit bereitstellen.
- [ ] Den News-/Blog-Bereich anhand § 18 Abs. 2 MStV prüfen und falls erforderlich eine geeignete redaktionell verantwortliche Person mit vollständigem Namen und Anschrift benennen.
- [ ] Verantwortlichen und gegebenenfalls Datenschutzbeauftragten eindeutig bestimmen.
- [ ] Für jeden Verarbeitungsvorgang Zweck, Rechtsgrundlage und – bei Art. 6 Abs. 1 lit. f DSGVO – dokumentiertes berechtigtes Interesse samt Abwägung festlegen.
- [ ] Bei Daten aus Discord/anderen Quellen die Informationspflicht nach Art. 14 DSGVO prüfen und rechtzeitig erfüllen.
- [ ] Vor Freigabe der öffentlichen Teamliste die individuelle Einwilligungs-UI und deren Textversion fachlich prüfen; Art. 13/14-Information, Minderjährigenschutz und Kontaktwege sicherstellen.
- [ ] Aufbewahrungsfrist für den minimalen Einwilligungs-/Widerrufsnachweis festlegen und automatisiert durchsetzen.
- [ ] Bei Minderjährigen vor Veröffentlichung klären, ob eine Zustimmung der Erziehungsberechtigten oder weitere Schutzmaßnahmen erforderlich sind; ohne Klärung nicht veröffentlichen.
- [ ] Hosting-Vertrag, Rechenzentrumsort, Unterauftragnehmer und Art.-28-Vertrag prüfen.
- [ ] Für Discord, Roblox, Gemini/OpenAI Empfängerrollen und mögliche internationale Übermittlungen mit konkretem Zielstaat und konkretem Transfermechanismus prüfen.
- [ ] Tatsächliche KI-Konfiguration, Provider-Auswahl, Modell, Datenfelder, Konto-/Tarifbedingungen, Aufbewahrung und Trainingsverwendung prüfen.
- [ ] Bewerbungs-, Team-, Moderations-, Audit- und Ticketdaten mit festen und technisch umgesetzten Speicher-/Löschfristen versehen.
- [ ] Löschung einschließlich Backups, Exporte und verlinkte Datenspeicher organisatorisch und technisch umsetzen.
- [ ] Betroffenenrechte mit Identitätsprüfung, Fristen, Zuständigkeit und Dokumentation praktisch ausführbar machen.
- [ ] Zuständige Datenschutzaufsichtsbehörde korrekt benennen.
- [ ] Cookie-/Storage-Einträge einzeln nach § 25 TDDDG einordnen; optionale Dienste vor Zustimmung blockieren.
- [ ] Prüfen, ob Kinder/Jugendliche die Angebote nutzen und ob zusätzliche Anforderungen für diesen konkreten Dienst gelten.

## 6. Nutzungsbedingungen und Einbeziehung

Die Nutzungsbedingungen sind ausführlicher strukturiert und enthalten unter anderem Zugang, Community-Verhalten, Inhalte/Rechte Dritter, Bewerbungen, Teamfunktionen, Moderation, KI, externe Plattformen, Verfügbarkeit, Datenschutz und eine zurückhaltende Haftungsregelung.

**Wichtig:** Eine verlinkte oder veröffentlichte Seite allein bedeutet nicht, dass AGB wirksam Bestandteil eines Vertrags geworden sind. § 305 Abs. 2 BGB verlangt bei Vertragsschluss einen deutlichen Hinweis, eine zumutbare Möglichkeit zur Kenntnisnahme und das Einverständnis der anderen Vertragspartei. Die tatsächlichen Registrierungs-, Bewerbungs- und Community-Abläufe müssen daraufhin geprüft werden. Klauseln müssen außerdem transparent und angemessen sein (§ 307 BGB); zwingende Haftungsgrenzen dürfen nicht umgangen werden (§ 309 BGB). Deshalb bleiben die Bedingungen bis zur Betreiberprüfung ein Entwurf.

## 7. Amtliche Rechtsgrundlagen für die abschließende Prüfung

- [§ 5 DDG – Allgemeine Informationspflichten](https://www.gesetze-im-internet.de/ddg/__5.html)
- [§ 18 MStV – Informationspflichten und Auskunftsrechte](https://www.landesrecht-bw.de/bsbw/document/jlr-NNLBW00007E9DNN00000000043)
- [DSGVO – Verordnung (EU) 2016/679, insbesondere Art. 13, 14, 28 und 44 ff.](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [§ 25 TDDDG](https://www.gesetze-im-internet.de/tdddg/__25.html)
- [§ 305 BGB – Einbeziehung Allgemeiner Geschäftsbedingungen](https://www.gesetze-im-internet.de/bgb/__305.html)
- [§ 307 BGB – Inhaltskontrolle](https://www.gesetze-im-internet.de/bgb/__307.html)
- [§ 309 BGB – Klauselverbote](https://www.gesetze-im-internet.de/bgb/__309.html)

Diese Quellen ersetzen keine Prüfung des konkreten Betreibers, der tatsächlichen Website-Inhalte, der Anbieter-Verträge und der betrieblichen Abläufe.
