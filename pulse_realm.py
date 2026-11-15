"""Audit #50: einheitliches, validiertes IC-/OOC-Modell für Vorgänge (Team-/Spielerfälle und Spielermeldungen).

* ``ic``  - Verstoß oder Vorgang im Spiel (In-Character, Ingame-Regelwerk)
* ``ooc`` - Verstoß oder Vorgang außerhalb des Spiels (Out-of-Character, Discord-Regelwerk)
* ``""``  - noch nicht zugeordnet (Altbestand); wird nie geraten, sondern in Listen als „offen“ gezeigt

Die Zuordnung ist eine Angabe pro Vorgang, die beim Anlegen validiert und später von berechtigten Personen nachgetragen werden
kann. Listen und Zählungen trennen strikt nach Bereich; ein Vorgang ist nie gleichzeitig IC und OOC.
"""
from __future__ import annotations

REALMS = {"ic": "IC (im Spiel)", "ooc": "OOC (Discord / außerhalb des Spiels)"}
UNASSIGNED_LABEL = "nicht zugeordnet"
FILTERS = ("ic", "ooc", "offen")


def normalize(value, *, allow_empty: bool = True) -> str:
    """``"ic"`` / ``"ooc"`` or ``""``; raises ``ValueError`` for anything else (and for empty values if ``allow_empty`` is false)."""
    text = str(value or "").strip().lower()
    if not text:
        if allow_empty:
            return ""
        raise ValueError("Bitte IC oder OOC wählen.")
    if text not in REALMS:
        raise ValueError("Ungültiger Bereich: erlaubt sind IC und OOC.")
    return text


def label(value) -> str:
    return REALMS.get(str(value or ""), UNASSIGNED_LABEL)


def matches(value, flt) -> bool:
    """Filter helper: ``flt`` ``ic``/``ooc`` match exactly, ``offen`` matches unassigned, anything else matches all."""
    flt = str(flt or "")
    value = str(value or "")
    if flt in ("ic", "ooc"):
        return value == flt
    if flt == "offen":
        return value == ""
    return True


def counts(rows, key: str = "realm") -> dict:
    out = {"ic": 0, "ooc": 0, "offen": 0}
    for row in rows or ():
        value = str((row or {}).get(key) or "")
        out[value if value in ("ic", "ooc") else "offen"] += 1
    return out
