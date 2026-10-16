# Pulse TeamOS – Änderungsprotokoll

## Version 7.8.19 – Warn-Sperren und Backups repariert

- Absturz bei der automatischen Entfernung abgelaufener Warn-Folgerollen behoben: Jede gespeicherte Folgerolle wird jetzt anhand ihrer eigenen Rollen-ID aufgelöst. Fehlerhafte Datensätze bleiben zur Prüfung erhalten, statt die komplette Bereinigung abzubrechen.
- Manuelle und automatische Server-Backups funktionieren wieder, wenn ein Server-Icon ausgelesen wurde; der zuvor falsche Variablenname hat den Erstellungsprozess mit `NameError` abgebrochen.
- Regressionstests sichern beide Korrekturen ab. Die aktuelle Namenspräfix-Synchronisierung hält Datei-I/O aus dem Discord-Event-Loop heraus, damit der Gateway-Heartbeat bei großen Mitgliedslisten nicht blockiert wird.

## Version 7.8.18 – Supportfälle zwischen Teammeetings

- Die Teamliste und jede Teamakte zeigen jetzt die Zahl der abgeschlossenen Pulse-Tickets pro Teammitglied seit dem letzten bestätigten Teammeeting-Zeitraum.
- Unter „Teambesprechung“ kann die Teamleitung nach dem Meeting den bisherigen Zeitraum archivieren und eine neue Zählung starten.
- Die abgeschlossene Wochenzählung wird intern gespeichert; die aktuelle Periode ist startinklusive und endexklusiv, damit Tickets nicht in zwei Zeiträumen gezählt werden.
- Es werden echte, abgeschlossene Tickets gezählt, die bei Abschluss einem Teammitglied zugeordnet waren; offene oder nicht übernommene Tickets erhöhen den Wert nicht.

## Version 7.8.17 – Wiederherstellung mit robuster API-Kompatibilität

- Stage-Kanäle werden mit der eigenen Discord-API-Signatur und erforderlichem Thema wiederhergestellt; bei älteren API-Versionen wird ein klarer Fallback protokolliert.
- Letzte Wiederherstellungs-Korrekturen aus 7.8.16 bleiben enthalten: Pulse-Sicherheitsrollen werden über dauerhafte ID-Aliase aufgelöst, und der normale Restore verändert vorhandene Mitgliedsrollen additiv statt sie ungefragt zu entfernen.

## Version 7.8.16 – Restore-Sicherheit und ID-Zuordnung

- Eingebaute Pulse-Warn-, Probezeit- und Pflichtrollen behalten nach dem Wiederherstellen gelöschter Rollen ihre Funktion: neue Discord-Rollen-IDs werden im Backup-Konfigurationsalias hinterlegt.
- Die normale Wiederherstellung behält zusätzlich vergebene Mitgliedsrollen bei; nur die ausdrücklich aktivierte exakte Wiederherstellung setzt zuweisbare Mitgliederrollen auf den gesicherten Stand zurück. Von Discord verwaltete Rollen werden nicht entfernt.
- Wiederherstellungen vermeiden unnötige identische Discord-API-Änderungen; die Sicherung selbst liest/schreibt größere Daten im Hintergrund, um den Bot-Heartbeat nicht unnötig zu blockieren.

## Version 7.8.15 – Vollständiges Discord- + Pulse-Backup

- Backup-Version 3 sichert Servername und unterstützte Servereinstellungen, Rollen inklusive Berechtigungsflags/Reihenfolge, Kategorien, Kanäle, Berechtigungsüberschreibungen, Kanaloptionen, Server-Icon und bestehende Mitglieder mit Spitznamen und Rollen-Zuordnungen.
- Pulse-Daten umfassen JSON-Konfigurationen, Pulse-Datenbankexport, Activity-Check-SQLite-Datenbank, Abmeldungen und verfügbare Ticket-Transkripte.
- Das Dashboard unterstützt JSON-Download, geprüftes JSON-Upload und eine Wiederherstellung von Discord-Struktur plus Pulse-Daten per Aktion.
- Vor jedem Restore wird automatisch eine komplette Rückfall-Sicherung erstellt. Optional kann die exakte Struktur erzwungen werden; dabei werden zusätzliche, nicht im Backup enthaltene und für den Bot verwaltbare Kanäle/Rollen entfernt.
- Uploads sind auf 50 MiB begrenzt, werden vor dem Speichern auf Server-ID, Format, Feldtypen, IDs und Datenbankdateien geprüft und bekommen einen servergenerierten Dateinamen. Restore/Import, Fehler und Wiederherstellungen landen im Audit-Log.
- Discord kann alte Nachrichten, private DMs, Webhooks, Einladungen, Integrationen, Boosts, nicht verfügbare Emojis oder ausgeloggte Mitglieder nicht vollständig rekonstruieren. Fehlende Mitglieder werden nicht erneut eingeladen; nicht verfügbare/verwaltete Discord-Rollen oder fehlende Bot-Berechtigungen werden als Restore-Warnung behandelt.

## Version 7.8.14 – Schutz vor missbräuchlichen Team-Verwarnungen

- Einheitlicher, persistenter Schutz für manuelle Team-Warnungen im Dashboard und über `/warn`.
- Warn-Spam wird blockiert: ab dem dritten Warnversuch gegen dasselbe Teammitglied innerhalb von fünf Minuten, ab vier Warnversuchen gegen mindestens drei Teammitglieder innerhalb von fünf Minuten oder ab sechs Warnversuchen in zehn Minuten.
- Beim ersten Verdachtsfall setzt Pulse eine 30-minütige Warn-/Teamaktionssperre und alarmiert die Führungsebene per Team-Update, Pulse-Inbox und Audit-Log. Wiederholte Vorfälle binnen 24 Stunden oder wiederholte Versuche trotz aktiver Sperre lösen einen automatischen Team-Kick aus.
- Der automatische Team-Kick entfernt nur konfigurierten Teamrollen und die Test-Supporter-Rolle. Er kickt und bannt niemanden vom Discord-Server. Wenn Discord-Berechtigungen oder Rollen-Hierarchie die Entfernung verhindern, protokolliert Pulse den Fehler und alarmiert die Führungsebene.
- Teamwarnungen aus `/warn` verwenden jetzt wie das Dashboard standardmäßig die festgelegte siebentägige Gültigkeit und führen dieselben Warn-Konsequenzen aus.
- Hochrangige bzw. gleichrangige Teamler können im Dashboard nicht mehr durch niedrigere Teamränge verwarnt werden; doppelte Warnanfragen innerhalb von vier Sekunden werden abgewiesen.
- Der Schutzstatus und die Ereignishistorie liegen in SQLite und werden von Pulse-Backups mitgenommen. Automatische Activity-Check-/Schichtwarnungen zählen nicht als menschliche Missbrauchsversuche.

## Version 7.8.13 – Feste Teamrang-Hierarchie für Namenspräfixe

- Die Rangreihenfolge aus den Pulse-Einstellungen ist jetzt ausdrücklich festgelegt: Founder/Inhaber/Führung, Entwicklung, Projektleitung, Projektkoordination, Direktion, Serverleitung, Management, Teamleitung, Teamaufsicht, Supervisor, Ausbildung, Community/Fraktionen, Administration, Moderation, Support und Bauteam.
- Erkannte Teamränge entscheiden anhand dieser Hierarchie – auch wenn eine allgemeine Discord-Rolle wie Bürger oder VIP in der Discord-Rollenliste technisch höher einsortiert ist. Bei mehreren Rängen gewinnt die höchste Teamrang-Stufe; Discord-Rollenpositionen brechen Gleichstände.
- Präfixe verwenden die gewünschten Kürzel, insbesondere `Inhaber`, `Stv.Inh`, `L.DK`, `DK`, `Stv.DK`, `L.MA`, `MA`, `Stv.MA`, `L.Supervisor`, `Supervisor` und `Stv.Supervisor`.
- Vorherige automatisch erzeugte Kürzel werden auf die neuen kanonischen Labels migriert; wirklich individuelle Präfixe bleiben erhalten.

## Version 7.8.12 – Einheitliche Rangpräfixe nach deiner Teamhierarchie

- Teamrollen verwenden jetzt die gewünschten Bezeichnungen, darunter `Founder`, `Inh.`, `Stv.Inh.`, `Rechte Hand`, `L.Dev`, `L.AB`, `AB`, `L.Admin`, `L.Mod`, `L.Sup`, `L.Bau` und `Stv.L.Bau`.
- Die höchste echte Discord-Rolle entscheidet weiterhin über das Präfix; dekorative Ebenen-/Trennrollen werden übersprungen.
- Bestehende gespeicherte Präfixe, die nur aus dem alten vollständigen Rollennamen oder früheren automatischen Kürzeln bestehen, werden auf die neuen Standardlabels umgestellt. Bewusst eigene Präfixe bleiben erhalten.
- Die Synchronisierung beim Bot-Start wendet die neuen Labels auch auf bereits vorhandene Mitglieder an.

## Version 7.8.11 – Kompakte Namenspräfixe für Teamrollen

- Automatische Namenspräfixe verwenden jetzt einheitliche kurze Bezeichnungen für die Teamhierarchie, unter anderem `PL`, `Stv.PL`, `L.PK`, `Stv.SL`, `Stv.TL`, `Stv.Sup`, `Sr.Mod`, `T.Sup` und die Bauteam-Ränge.
- Die vollständigen Discord-Rollennamen und die manuell in den Einstellungen konfigurierten Präfixe bleiben unverändert.
- Dekorative Trennrollen wie `━━━━ Projektleitung-Ebene ━━━━` werden nicht mehr als Rangpräfix behandelt, damit sie den tatsächlichen Teamrang nicht übersteuern.
- Beim regulären Startup-Abgleich erhalten bereits vorhandene Mitglieder die neuen kurzen Präfixe ebenfalls. Die Nickname-Synchronisierung läuft dabei weiter mit gecachtem Zustand und nichtblockierender Datei-I/O.

## Version 7.8.10 – Namenspräfix für ausgeschiedene Teamler

- Die Rolle `1489912887949394082` erzwingt jetzt das Namenspräfix `𝐄𝐗 𝐓𝐄𝐀𝐌𝐋𝐄𝐑 ✗`, auch wenn noch eine höher sortierte Discord-Rolle vorhanden ist.
- Damit erhalten ehemalige Teammitglieder nach einem Team-Kick nicht versehentlich erneut ein aktives Rollenpräfix wie Owner oder Teamleitung.
- Die Regel gilt auch für bestehende Mitglieder, sobald Pulse die Namenspräfix-Synchronisierung ausführt.

## Version 7.8.9 – Stabiler Mitglieder-Namensabgleich

- Die einmalige Synchronisierung tausender bestehender Mitglieder lädt Konfiguration und Nickname-Zustände nicht mehr bei jedem Mitglied erneut synchron aus der JSON-Datei.
- Dateizugriffe laufen außerhalb des Discord-Ereignisloops; dadurch soll der Gateway-Heartbeat während des Backfills nicht mehr durch lokale Dateioperationen blockieren.
- Zustände werden aus einem Cache in kleinen Paketen gespeichert. Schreibvorgänge verwenden eine temporäre Datei mit anschließendem atomarem Austausch, damit paralleles Lesen keine halbfertige JSON-Datei sieht.
- Wird eine alte beschädigte Nickname-Zustandsdatei erkannt, sichert Pulse sie als `*.corrupt-*.json`, anstatt den JSON-Fehler den Synchronisierungs-Task abbrechen zu lassen.

## Version 7.8.8 – Beförderungs-Probezeit und befristete Team-Warnungen

- Jede Beförderung verlangt jetzt eine ausdrückliche Probezeit-Auswahl von 3 oder 7 Tagen. Pulse speichert die vorherige Teamrolle und beendet die Probezeit automatisch nach Ablauf.
- Erhält ein Mitglied während aktiver Probezeit eine Team-Warnung, wird die vorherige Teamrolle wiederhergestellt. Erhält ein Test Supporter eine Team-Warnung, entfernt Pulse automatisch seine Teamrollen (Team-Kick, kein Discord-Server-Kick).
- Team-Warnungen aus dem Dashboard haben eine einstellbare Laufzeit von 1 bis 365 Tagen (Standard 7 Tage). Automatische Warnungen erhalten ebenfalls eine 7-Tage-Laufzeit; abgelaufene Warnungen bleiben in der Historie, zählen nicht mehr zur Warnstufe und die Discord-Warnrolle wird synchronisiert.
- Zeitlich befristete Warn-Folgerollen übernehmen das Ablaufdatum ihrer Warnung. Sie bleiben erhalten, solange weitere aktive Team-Warnungen bestehen, und werden entfernt, sobald keine aktive Warnung mehr verbleibt.
- Warn-4-Downranks und normale Warn-5-Team-Kicks werden beim Ablauf bzw. Zurückziehen aller aktiven Team-Warnungen automatisch rückgängig gemacht, sofern Pulse die vorherige Rolle gespeichert hat und die Rolle nicht zwischenzeitlich manuell geändert wurde. Ein Test-Supporter-Team-Kick bleibt als Maßnahme während der Probezeit dauerhaft bestehen und stellt den Bewerber nicht automatisch wieder ein.
- Die Namenspräfix-Synchronisierung lädt die Discord-Mitgliederliste beim Start erneut. Wenn Discord für einen Namen mit vorhandenem Rang-Tag vorübergehend nur `@everyone` liefert, setzt Pulse nicht fälschlich das Standardpräfix `Bürger ✗`, sondern lädt die Rollen erneut und verschiebt die Änderung bei unvollständigen Daten.

## Version 7.8.7 – Höchste Rolle bestimmt den Namenspräfix

- Pulse verwendet nun immer die höchste zugewiesene Discord-Rolle für den Namenspräfix, selbst wenn für diese Rolle kein manueller Präfix gespeichert ist.
- Individuell konfigurierte Präfixe bleiben als Überschreibung erhalten; ein niedrigeres konfiguriertes Rollenprofil kann eine höhere Discord-Rolle nicht mehr übersteuern.
- Häufige Rollen-Dekorationen wie `》BORP メ Owner` werden automatisch zu lesbaren Präfixen wie `Owner ✗` und `》 𝐁𝐎 ║ Bürger` zu `Bürger ✗`.
- Bestehende, manuell gesetzte Rangteile wie `Sr.-Sup メ Dawo`, `Owner メ Phillip`, `TeamleitungメMrJoela` oder `LTA ✗ Max` werden bei der Synchronisierung erkannt und vor dem neuen Präfix entfernt.
- Die Start-Synchronisierung erfasst weiterhin alle bestehenden menschlichen Mitglieder, auch wenn keine manuellen Rollenpräfixe konfiguriert sind.

## Version 7.8.6 – Bestehende Nicknames automatisch synchronisieren

- Beim ersten Ready-Ereignis nach dem Bot-Start startet automatisch eine Synchronisierung aller passenden bestehenden Mitglieder, nicht nur neu beigetretener Nutzer.
- Pulse fordert bei Bedarf die vollständige Mitgliederliste des konfigurierten Servers an, damit die Synchronisierung nicht nur auf einen unvollständigen Cache begrenzt bleibt.
- Änderungen an Rollenpräfixen und am Standardpräfix stoßen sofort eine Synchronisierung bestehender Nicknames an. Wird die Konfiguration während einer laufenden Synchronisierung geändert, wird ein weiterer Durchlauf vorgemerkt.
- Die Synchronisierung bleibt auf Menschen beschränkt, berücksichtigt die höchste konfigurierte Discord-Rolle und respektiert weiterhin Discords Nickname-Limit und Berechtigungs-Hierarchie.

## Version 7.8.5 – Automatische Rollen-Namenspräfixe

- Rollenpräfixe können in den bestehenden Pulse-Einstellungen für jede normale Discord-Rolle konfiguriert werden, z. B. `Bürger ✗` oder `VIP ✗`.
- Der Standardpräfix `Bürger ✗` gilt bereits ohne zusätzliche Rollenregel für alle neuen Mitglieder und als Fallback für Mitglieder ohne höher priorisierte Präfixrolle; er kann in den Einstellungen geändert oder deaktiviert werden.
- Pulse setzt den Präfix beim Serverbeitritt und nach Rollenänderungen automatisch vor den Nickname. Hat ein Mitglied mehrere konfigurierte Rollen, gilt die höher positionierte Discord-Rolle.
- Wenn die passende Rolle entfernt wird, versucht Pulse den vorherigen Nickname wiederherzustellen. Manuelle Nickname-Änderungen werden als neue Ausgangsbasis übernommen.
- Die Einstellungen enthalten eine Synchronisierung für bereits vorhandene Mitglieder. Nickname-Änderungen benötigen die Discord-Berechtigung „Spitznamen verwalten“ sowie eine entsprechend hohe Bot-Rolle.
- Die Discord-Grenze von 32 Nickname-Zeichen wird berücksichtigt; sehr lange Namen können nach dem Präfix gekürzt werden.

## Version 7.8.4 – Live-Serverstatistiken in Sprachkanälen

- Pulse erstellt unter der bestehenden Kategorie `1536812484462256179` vier Statistik-Sprachkanäle: Mitgliederzahl, Boost-Anzahl, Bot-Anzahl und Rollenanzahl.
- Die Kanalnamen werden automatisch nach Beitritten, Austritten und Boost-Änderungen aktualisiert; Ereignisse werden kurz gebündelt, damit nicht unnötig viele Discord-Änderungen gesendet werden.
- Die Sprachkanäle kopieren die Sichtbarkeitseinstellungen der Kategorie und verweigern `Verbinden` für @everyone sowie vorhandene Rollen-/Mitglieder-Überschreibungen. Discord-Administratoren können Kanal-Sperren technisch weiterhin umgehen.
- Benötigt wird die Discord-Berechtigung `Kanäle verwalten` für die Zielkategorie.

## Version 7.8.3 – Honeypot-Schutzkanal

- Neuer Honeypot für den Kanal `1560397792751657053`: Pulse stellt dort automatisch ein auffälliges Embed mit der Warnung bereit, dort auf keinen Fall zu schreiben.
- Wer dort selbst eine Nachricht sendet, dessen Nachricht wird nach Möglichkeit gelöscht und der Account automatisch vom Discord-Server gebannt.
- Pulse ignoriert ausschließlich die eigene Warnnachricht, damit der Honeypot nicht beim Start seinen eigenen Schutz auslöst.
- Ban-Versuche, Berechtigungsprobleme und nicht bannbare Ziele werden in der Hosting-Konsole protokolliert. Für das Webhook-Posting wird die Nachricht gelöscht; Webhooks selbst sind keine Mitglieder und können nicht mit einem Nutzer-Ban erfasst werden.
- Benötigte Discord-Berechtigungen: Kanal ansehen, Nachrichtenverlauf anzeigen, Nachrichten senden, Links einbetten, Nachrichten verwalten und Mitglieder bannen.

## Version 7.8.2 – Zwischenrollen in der aktiven Pulse-Pro-Einstellungsseite

- Der sichtbare `/settings`-Bereich wird von Pulse Pro überschrieben; die digitalen Zwischenrollen befinden sich jetzt in genau dieser aktiven Einstellungsseite und erscheinen oberhalb der einzelnen Discord-Rollen-Rechtekarten.
- Digitale Zwischenrollen können dort erstellt, umbenannt, konfiguriert und gelöscht werden. Rechte werden über sämtliche Pulse-Pro-Berechtigungen einschließlich Ankündigungen, Übergaben, Beförderungsanträgen, Teamstatus und Activity Check ausgewählt.
- Jedem Profil lassen sich eine oder mehrere vorhandene, unter „Teamrollen“ ausgewählte Discord-Teamrollen zuweisen. Mitglieder erhalten das Profil automatisch über ihre Discord-Rollen.
- Speichern und Löschen sind serverseitig auf Administratoren beschränkt; alle Änderungen werden im Audit-Log protokolliert.
## Version 7.8.1 – Digitale Rollen nach Discord-Teamrollen zuordnen

- Digitale Dashboard-Rollen werden jetzt direkt vorhandenen, unter „Team-Rollen“ konfigurierten Discord-Teamrollen zugewiesen – nicht einzelnen Personen.
- Beim Erstellen oder Bearbeiten eines digitalen Profils kannst du eine oder mehrere Discord-Teamrollen auswählen und Berechtigungen wie Tickets, Bewerbungen, Verwarnungen, Teamaktionen, Schulungen, Wiki und Analytics bündeln.
- Alle Mitglieder mit den verknüpften Discord-Teamrollen erhalten die digitalen Profilrechte automatisch; mehrere passende Profile werden kombiniert.
- Die Zuordnung bleibt rein intern in Pulse: Es werden keine Discord-Rollen erstellt, vergeben, entfernt oder in ihrer Hierarchie verändert.
- Das direkte Zuweisen eines Dashboard-Profils per Nutzer-ID wurde aus der Oberfläche entfernt. Bestehende Discord-Rollenrechte bleiben weiterhin parallel verfügbar.

## Version 7.8.0 – Dashboard-only Berechtigungsrollen

- Administratoren können unter `/settings` beliebig viele wiederverwendbare Pulse-Dashboard-Profile anlegen, bearbeiten und löschen, z. B. Support, Personalverwaltung oder Leitung.
- Jedes Profil bündelt die bestehenden Dashboard-Berechtigungen. Ein Profil kann mehreren Teamlern zugewiesen werden; einem Teamler können auch mehrere Profile zugeordnet werden.
- Die Zuweisung funktioniert über Discord-Nutzer-ID, Mention oder Namen und ist auf Mitglieder mit einer konfigurierten Teamrolle beschränkt. Einzelne Zuweisungen lassen sich wieder entziehen.
- Diese Profile sind rein intern: Sie ändern weder Discord-Rollen noch Serverberechtigungen oder Rollen-Hierarchie. Serverinhaber und Discord-Administratoren behalten weiterhin den technischen Adminzugriff.
- Bestehende Discord-Rollenrechte bleiben erhalten; beide Berechtigungsarten werden in der zentralen serverseitigen Rechteprüfung zusammengeführt.

## Version 7.7.9 – Verbindliches Bewerbungsgespräch
- Die erste Annahme einer Bewerbung ist jetzt eine Einladung zum verpflichtenden Bewerbungsgespräch und keine Einstellung.
- Dabei wird ausschließlich die Bewerberolle `1496070682126188604` vergeben. Es wird noch keine Team-Einstiegsrolle vergeben, keine Teamakte auf Einstellung gesetzt und keine Teamzugehörigkeit gestartet.
- Eingeladene Bewerber bleiben unter `/applications` in der aktiven Übersicht. Erst über die separate Aktion „Gespräch geführt – einstellen“ kann eine Team-Einstiegsrolle nach dokumentierter Gesprächsnotiz vergeben werden.
- Nach einer Einstellung wird die Bewerberolle entfernt; alle anderen nicht betroffenen Discord-Rollen bleiben erhalten. Eine Ablehnung nach dem Gespräch entfernt die Bewerberolle ebenfalls.
- DM, Team-Update, Bewerbungsstatus und Audit-Protokoll unterscheiden Einladung zum Gespräch von der endgültigen Einstellung.

## Version 7.7.8 – Automatische Einstellungsrolle
- Bei einer angenommenen Bewerbung vergibt Pulse zusätzlich die feste Discord-Rolle `1496070682126188604` zur gewählten Team-Einstiegsrolle.
- Die automatische Rolle wird vorab auf Existenz, Verwaltbarkeit und Bot-Rollenhierarchie geprüft. Fehlt sie oder kann der Bot sie nicht vergeben, wird die Einstellung mit einer klaren Fehlermeldung nicht stillschweigend als erfolgreich markiert.
- Die zusätzliche Rolle bleibt beim Austausch bisheriger Team-Einstiegsrollen erhalten und wird in Bewerbungsakte, Team-Update, DM und Audit-Protokoll aufgeführt.
- Regressionstests sichern die automatische Rollenvergabe ab.

## Version 7.7.7 – Anspruchsvolle Eignungstests
- Low Team: 12 schwierige Fragen, Bestehensgrenze 75 %.
- High Team: 22 schwierige Fragen, Bestehensgrenze 82 %.
- Leitungsebene: 30 sehr schwierige Fragen, Bestehensgrenze 90 %.
- Die Fragen verwenden konkrete Fallbeispiele aus dem IC-/RP-Regelwerk und dem Discord-Regelwerk: unter anderem NLR, Fear-RP/NVL, Meta-/Powergaming, Safezones, Peacetime, Geiselnahmen, Medics, Verkehr, Support, Datenschutz, Doxing, Discord-Tickets, interne Leaks und Berechtigungen.
- Antwortoptionen werden beim Anzeigen gemischt; der Lösungsschlüssel wird nicht in das HTML geschrieben. Bewertung und Pflichtfragenzahl werden serverseitig anhand der gewählten Laufbahn bestimmt.
- Testauswertungen speichern zusätzlich Themenbereich und Regelstellen für eine nachvollziehbare Sichtung in der Bewerbungsakte.

## Version 7.7.6 – Discord-OAuth für Bewerbungen
- Der Bewerbungslogin nutzt standardmäßig dieselbe, bereits registrierte Callback-URL wie das Dashboard. Dadurch ist keine zusätzliche Discord-Redirect-URI nur für `/apply/callback` nötig.
- Der gemeinsame `/callback`-Endpunkt unterscheidet den Bewerbungslogin anhand des separaten OAuth-State-Cookies und gibt Bewerber sicher an `/apply` zurück, ohne ihnen eine Dashboard-Session zu erteilen.
- `DISCORD_APPLICATION_REDIRECT_URI` bleibt als Fallback für Deployments verfügbar, die keine `DISCORD_REDIRECT_URI` gesetzt haben.
- Regressionstests decken die Callback-Auswahl und den getrennten Bewerbungs-Session-Ablauf ab.

## Version 7.7.5 – Bewerbungsportal

- Das externe Bewerbungsformular unter `/apply` ist jetzt ausschließlich für Mitglieder mit Discord-Rolle `1531303733311307997` freigeschaltet.
- Die Rollenzugehörigkeit wird nach Discord-OAuth sowohl beim Öffnen des Formulars als auch serverseitig beim Absenden geprüft. Manipulierte Formularfelder umgehen die Prüfung nicht; bei nicht prüfbarer Mitgliedschaft wird fail-closed abgebrochen.
- Eingereichte Bewerbungen werden weiterhin in `applications.json` gespeichert und sind danach im geschützten Pulse-Bereich unter `/applications` sichtbar.
- Nach erfolgreicher Speicherung sendet Pulse eine Team-Benachrichtigung in Kanal `1558776676731719720` und pingt gezielt Rolle `1531300183088824340`. Die Benachrichtigung enthält Bewerber, Laufbahn, Testergebnis und einen Link zur Bewerbungsübersicht, aber nicht den privaten Motivationstext.
- Benachrichtigungsfehler löschen oder verlieren keine bereits gespeicherte Bewerbung; der Fehler wird in der Hosting-Konsole protokolliert.
- Die Entscheidungsoberfläche wird auch für Teamrollen mit explizitem Recht `can_manage_applications` angezeigt, nicht nur für Beförderungsberechtigte oder Administratoren.
- Neue Regressionstests prüfen die Rollen-Gate auf GET/POST, die Benachrichtigungsziele und die eingeschränkten Discord-Mentions.

## Version 7.7.4

### Stabilität und Betrieb
- Das Dashboard verwendet dieselbe zentrale Versionsquelle wie Pulse Pro.
- Beim Beenden des Bots wird der eingebettete Uvicorn-Webserver kontrolliert beendet; bei einem festhängenden Shutdown wird der Task nach einem begrenzten Zeitfenster abgebrochen.
- Die Webseite stellt ein lokales Favicon bereit, statt auf die automatische Browseranfrage mit einer 404-Antwort zu reagieren.
- Die Teamliste vergleicht alle sichtbaren Embed-Bestandteile, bevor eine Discord-Nachricht bearbeitet wird.
- Bei Discord-HTTP-429-Limits liest Pulse `Retry-After`, wiederholt höchstens einen begrenzten Versuch und beendet den aktuellen Durchlauf bei längeren Limits, statt weitere API-Aufrufe zu stapeln.
- Die Abmeldungsverwaltung prüft Nickname-Berechtigungen und Rollen-Hierarchie vor API-Aufrufen. Wiederholte, vorhersehbar verbotene Änderungen werden nicht bei jedem Abgleich erneut angefordert. Eine geänderte Rollen-/Rechtekonfiguration lässt die Prüfung wieder anlaufen.
- Die Abmeldungsliste überspringt identische Embed-Updates, um unnötige PATCH-Anfragen zu reduzieren.
- Formularaktionen der Website stellen die Scrollposition auf derselben Seite wieder her.

### Rechte und Sicherheit
- Der Rechte-Reset deaktiviert die gespeicherten Pulse-Berechtigungen für Rollen und markiert den Reset als aktiv.
- Solange dieser Reset aktiv ist, werden neue ausgewählte Teamrollen nicht automatisch mit Standardrechten versehen.
- Die automatische Vergabe empfohlener Rechteprofile ist während des aktiven Resets gesperrt. Ein Administrator kann später Rechte gezielt an einzelnen Rollen in deren Rechtekarten setzen.
- Serverinhaber und echte Discord-Administratoren behalten den technischen Adminzugriff. Das ist getrennt von den gespeicherten Pulse-Rollenrechten.

### Tests
GitHub Actions prüft Syntax, bestehende Feature-Regressionen, den Smoke-Test und die Unit-Tests. Die aktuellen Prüfungen für Version 7.7.4 sind erfolgreich.

## Update-Hinweise für Bot-Hosting
1. Repository-Branch `main` abrufen und den Bot-Prozess neu starten.
2. Nach dem ersten Start als Serveradministrator einmal die Pulse-Einstellungen öffnen. Falls der gespeicherte Berechtigungsstand noch eine ältere Profilversion besitzt, wird der Sicherheitsreset dort automatisch ausgeführt.
3. In den Einstellungen prüfen, dass die normalen Rollenrechte leer sind. Änderungen am produktiven Discord-Server und die tatsächliche Verfügbarkeit aller Funktionen müssen anschließend live im eigenen Server geprüft werden.
