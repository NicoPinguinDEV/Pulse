---
name: pulse-verify
description: Manually verify Pulse changes before a commit, release, or deployment. Use only when the user invokes /pulse-verify.
disable-model-invocation: true
argument-hint: [targeted|full]
---

# Pulse-Verifikation

Nur prüfen, nicht committen, pushen, deployen oder Dateien automatisch ändern.

1. Prüfe aktuellen Branch, HEAD, git status --short und git diff --check. Bewahre alle vorhandenen Änderungen.
2. Wenn $ARGUMENTS "targeted" ist, bestimme anhand der Diff-Dateien die passenden vorhandenen Regressionstests und führe diese sowie compileall aus. Wenn kein klarer Test zugeordnet werden kann, suche zuerst nach ähnlichen Tests.
3. Standardmäßig oder bei $ARGUMENTS "full" führe die gleichen Qualitätsschritte aus wie .github/workflows/python-syntax.yml:
   - python -m ruff check .
   - python -m compileall -q .
   - python tests/feature_smoke.py
   - python tests/smoke_test.py
   - python -m unittest discover -s tests -p 'test_*.py'
4. Starte keine Tests, die echte Discord-/OAuth-/Produktionsaktionen auslösen. Wenn ein Test das erfordert, überspringe ihn, nenne den Grund und suche eine sichere Alternative.
5. Reporte jeden tatsächlich ausgeführten Check mit bestanden/fehlgeschlagen/übersprungen. Behaupte nie, dass die Tests bestanden sind, wenn sie nicht ausgeführt wurden. Fehler nicht verschweigen und bei fehlgeschlagenen Checks keine Freigabe für Deployment suggerieren.
6. Antworte kompakt auf Deutsch mit Ergebnis und konkreter nächster Aktion.