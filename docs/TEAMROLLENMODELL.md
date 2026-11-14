# BOCHUM RP Teamrollen

Die Teamrollen in Pulse werden zentral in `pulse_team_roles.py` beschrieben. Die Reihenfolge entspricht der aktuell gemeldeten Discord-Hierarchie; Rollennamen werden vor dem Abgleich von BOCHUM-Dekorationen und Unicode-Schriftvarianten normalisiert.

## Führungsebene

1. Founder
2. Rechte Hand
3. Owner
4. Co.Owner
5. Projektleitung
6. Stv.Projektleitung
7. Serverleitung
8. Stv.Serverleitung

## HighTeam

1. Management
2. Stv.Management (einschließlich der Schreibweise `Stv.-Management`)
3. Teamleitung
4. Stv.Teamleitung
5. Ausbilder
6. Fraktionsverwaltung
7. Eventleitung

## LowTeam

1. Sr.Admin
2. Admin
3. Jr.Admin
4. Sr.Moderator
5. Moderator
6. Jr.Moderator
7. Sr.Supporter
8. Supporter
9. Test Supporter

## Technische Regeln

- Diese Datei enthält Rollennamen und Rangfolge, keine Discord-Rollen-IDs. IDs neu erstellter Rollen bleiben in den Laufzeit-Konfigurationen bzw. der TeamBot-Rollenverwaltung.
- TeamBot-Kategorien bleiben `fuehrungsebene`, `highteam` und `lowteam`.
- Team-Warnungen können ab `Teamleitung` vergeben bzw. verwaltet werden; `Stv.Teamleitung` liegt darunter.
- Die Empfehlung für Dashboard-Rechte richtet sich nach der konkreten aktuellen Rolle. Insbesondere erhalten `Jr.Admin`, `Admin` und `Sr.Admin` nicht automatisch die vollständigen Führungsrechte.
- Die Fraktionsverwaltung wird über die aktuelle Rolle `Fraktionsverwaltung` erkannt, sodass ein Austausch ihrer Discord-ID die Berechtigung nicht ungewollt unterbricht.
- Historische Rollenbezeichnungen dienen nur noch dazu, alte Nickname-Präfixe zu entfernen. Sie sind keine aktiven Rang-Aliase und gewähren keine Berechtigungen.
- Eine neue oder ersetzte Discord-Rolle muss weiterhin in der Pulse-Teamrollen-Konfiguration bzw. in der passenden TeamBot-Liste ausgewählt werden. Der Code erfindet oder errät keine Discord-IDs.
