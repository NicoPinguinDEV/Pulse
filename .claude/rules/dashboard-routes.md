---
paths:
  - webserver.py
  - pulse_permissions.py
  - pulse_security.py
  - pulse_sessions.py
---

# Regeln für Dashboard- und API-Änderungen

- Bei jedem Endpunkt zuerst alle Implementierungen/Registrierungen mit derselben Route und HTTP-Methode suchen. Module wie pulse_pro können bestehende Handler zur Laufzeit ersetzen; die effektiv aktive Route muss geklärt werden.
- Den tatsächlichen Ablauf prüfen: Authentifizierung → serverseitige Autorisierung → Eingabevalidierung → Daten/Discord-Aktion → Audit/Antwort.
- Berechtigungen nicht nur in der UI verstecken. Direkte API-Aufrufe ohne passende Rechte müssen in Tests abgewiesen werden.
- Bestehende OAuth-Redirects, Session-/CSRF-Schutz, Cookies, Rollenhierarchie und Audit-Verhalten bewahren.
- webserver.py nicht vollständig in den Kontext kopieren. Nutze gezielte Suche und kleine Zeilenbereiche.
- Für Änderungen TestClient/Mocks verwenden; Tests dürfen nicht live Discord oder Produktionsdaten ändern.