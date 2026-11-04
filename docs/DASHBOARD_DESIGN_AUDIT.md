# Dashboard-Design-Audit – Phase 18.4

**Arbeitsbranch:** `quality/dashboard-design`  
**Ausgangsstand:** `4743745be7416d653257b8a71c07330f633f4d72`  
**Ziel:** Gemeinsame Dashboard-Designsprache schrittweise vereinheitlichen, ohne Routen, Rechte, Datenformate oder gespeicherte Nutzereinstellungen zu verändern.

## Festgelegte Designbasis

- Anthrazit bleibt die dunkle Oberflächenbasis; der helle Modus behält seine hellen Flächen.
- Coral-Farbskala: `#B94732` als kontrastreicher Primärton und `#BE4A34` als zweite dunkle Coral-Stufe für weiße Button-Schrift; `#FF765C` und `#FF947B` dienen als helle Akzente und Glows.
- Erfolg, Warnung und Fehler behalten eigene semantische Farben.
- Discord-Vorschauen dürfen Discords originale Blurples verwenden; sie sind keine Pulse-Akzentfarben.
- Bereits gespeicherte, vom Administrator konfigurierte Branding-Farben werden nicht automatisch migriert.

## In diesem ersten Änderungenblock

- `pulse_pro.py`: globale Light-/Dark-Tokens, Hero, Sidebar, Buttons, Fokuszustände, Partikel, Cursor, Kalender-Akzent und Web-App-Theme-Farbe auf Anthrazit/Coral ausgerichtet; aktive alte Rot- und Indigo-/Violett-Akzente entfernt.
- `pulse_design.py`: ältere eingebettete Karten, Heroes, Buttons und Formulare folgen den gemeinsamen Tokens. Die blaue Informationskennzeichnung bleibt bewusst eine semantische Infofarbe.
- `pulse_ultimate.py`: Coral als Standard für neu angelegte Branding- und Abteilungsfarben; bestehende gespeicherte Einstellungen bleiben unberührt.
- `pulse_home.py`, `pulse_ticket_builder.py`, `pulse_admin.py`, `pulse_guard_admin.py`, `pulse_factions_dashboard.py` und `pulse_phase7.py`: konkrete Dashboard-Akzentstellen angepasst.

## Bewusst nicht verändert

- `cogs/pulse_suite.py` enthält eine alte CSS-Konstante, die im Modul derzeit keinen Verwendungsaufruf hat. Sie wird nicht als aktive Oberfläche behandelt und nicht blind umgebaut oder gelöscht.
- Farben in Discord-Nachrichten- und Embed-Vorschauen bleiben dort unverändert, wo sie Discord selbst darstellen.
- Die öffentliche Statusseite bleibt eine eigenständige, nicht authentifizierte Seite; nur ihre neutrale Marken-Akzentfarbe wurde angepasst.
- Keine Route, Discord-Aktion, Berechtigungsregel, Datenbankstruktur oder Produktivdatei wird durch diesen Änderungenblock verändert.

## Kontrastprüfung

- Die erste Coral-Primärfarbe `#D95641` erreichte nur ungefähr 3,92:1 Kontrast gegenüber Weiß und wird daher nicht für weiße Button-Schrift verwendet.
- Die angepassten Primärstufen `#B94732` und `#BE4A34` erreichen ungefähr 5,24:1 bzw. 4,98:1 auf Weiß.
- Der dunkle Modus nutzt helle Coral-Töne für Textakzente; Primärbuttons bleiben auf ausreichend dunklen Coral-Stufen.
- Die Berechnung ist als Regressionstest im Repository abgesichert.

## Test- und Abnahmestatus

- Source-Level-Regressionstests prüfen die Coral-Tokens, ausgewählte Legacy-Akzentstellen und den Kontrast der Primärfarben gegen weiße Button-Schrift (mindestens 4,5:1).
- GitHub Actions müssen die Tests auf diesem Branch ausführen; bis dahin ist der Änderungenblock nicht als bestanden zu werten.
- Playwright-Desktop-/Mobilprüfungen und eine seitenweise visuelle Abnahme bleiben Bestandteil der weiteren Phase 18.4.
- Es wurde kein Merge und kein VPS-Deployment ausgelöst.
