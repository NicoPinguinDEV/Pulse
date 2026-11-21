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

## Automatische Rollenvergabe bei manueller Einstellung

Die Dashboard-Funktion „Teamler manuell einstellen“ vergibt die ausgewählte Rangrolle zusammen mit drei Zusatzrollen: der allgemeinen Teamler-Rolle, der Positions-Ebene und einer der vier Gruppenrollen. Die IDs liegen zentral in `pulse_ids.py`; die Zuordnung nach dem normalisierten Rangnamen steht in `pulse_hire_roles.py`.

| Aktuelle Teamposition | Positions-Ebene | Gruppenrolle |
|---|---|---|
| Test Supporter, Supporter, Sr.Supporter | Sup Ebene | Low-Team |
| Jr.Moderator, Moderator, Sr.Moderator | Mod Ebene | Low-Team |
| Jr.Admin, Admin, Sr.Admin | Admin Ebene | Low-Team |
| Eventleitung | Eventleitung-Ebene | High-Team |
| Fraktionsverwaltung | Fraktionsverwaltung-Ebene | High-Team |
| Ausbilder | Ausbilder-Ebene | High-Team |
| Teamleitung, Stv.Teamleitung | Teamleitung-Ebene | High-Team |
| Management, Stv.Management | Management-Ebene | High-Team |
| Serverleitung, Stv.Serverleitung | Serverleitungs-Ebene | Leitungsebene |
| Projektleitung, Stv.Projektleitung | Projektleitungs-Ebene | Leitungsebene |
| Owner, Co.Owner, Founder | Owner-Team | Führungsebene |
| Rechte Hand | Rechte-Hand-Ebene | Führungsebene |

Diese Gruppenrollen sind reine zusätzliche Discord-Zugehörigkeitsrollen. Sie ersetzen nicht die bestehenden drei TeamBot-Kategorien und werden nicht in `team_role_ids` eingetragen. Die für die Dashboard-Berechtigungen maßgebliche Rangrolle bleibt unverändert.

## Technische Regeln

- Diese Datei enthält Rollennamen und Rangfolge, keine Discord-Rollen-IDs. Die eigentlichen Rangrollen bleiben in der Laufzeit-Konfiguration bzw. der TeamBot-Rollenverwaltung; fest zugeordnete Zusatzrollen-IDs liegen zentral in `pulse_ids.py`.
- TeamBot-Kategorien bleiben `fuehrungsebene`, `highteam` und `lowteam`.
- Team-Warnungen können ab `Teamleitung` vergeben bzw. verwaltet werden; `Stv.Teamleitung` liegt darunter.
- Die Empfehlung für Dashboard-Rechte richtet sich nach der konkreten aktuellen Rolle. Insbesondere erhalten `Jr.Admin`, `Admin` und `Sr.Admin` nicht automatisch die vollständigen Führungsrechte.
- Die Fraktionsverwaltung wird über die aktuelle Rolle `Fraktionsverwaltung` erkannt, sodass ein Austausch ihrer Discord-ID die Berechtigung nicht ungewollt unterbricht.
- Historische Rollenbezeichnungen dienen nur noch dazu, alte Nickname-Präfixe zu entfernen. Sie sind keine aktiven Rang-Aliase und gewähren keine Berechtigungen.
- Eine neue oder ersetzte Discord-Rolle muss weiterhin in der Pulse-Teamrollen-Konfiguration bzw. in der passenden TeamBot-Liste ausgewählt werden. Der Code erfindet oder errät keine Discord-IDs.
