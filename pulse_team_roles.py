"""Canonical current team-role model for © BOCHUM RP | V1.

This module contains names and ordering only. Discord IDs stay in runtime configuration:
newly created/recreated roles are resolved by their current decorated names.
"""
from __future__ import annotations

import re
import unicodedata

TEAM_ROLE_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("fuehrungsebene", "Führungsebene", (
        "Founder",
        "Rechte Hand",
        "Owner",
        "Co.Owner",
        "Projektleitung",
        "Stv.Projektleitung",
        "Serverleitung",
        "Stv.Serverleitung",
    )),
    ("highteam", "HighTeam", (
        "Management",
        "Stv.Management",
        "Teamleitung",
        "Stv.Teamleitung",
        "Ausbilder",
        "Fraktionsverwaltung",
        "Eventleitung",
    )),
    ("lowteam", "LowTeam", (
        "Sr.Admin",
        "Admin",
        "Jr.Admin",
        "Sr.Moderator",
        "Moderator",
        "Jr.Moderator",
        "Sr.Supporter",
        "Supporter",
        "Test Supporter",
    )),
)

TEAM_ROLE_ORDER: tuple[str, ...] = tuple(
    role_name
    for _group_key, _group_label, role_names in TEAM_ROLE_GROUPS
    for role_name in role_names
)

TEAM_ROLE_PREFIXES: dict[str, str] = {
    "Founder": "Founder",
    "Rechte Hand": "Rechte Hand",
    "Owner": "Owner",
    "Co.Owner": "Co.Owner",
    "Projektleitung": "PL",
    "Stv.Projektleitung": "Stv.PL",
    "Serverleitung": "SL",
    "Stv.Serverleitung": "Stv.SL",
    "Management": "MA",
    "Stv.Management": "Stv.MA",
    "Teamleitung": "TL",
    "Stv.Teamleitung": "Stv.TL",
    "Ausbilder": "AB",
    "Fraktionsverwaltung": "FV",
    "Eventleitung": "EL",
    "Sr.Admin": "Sr.Admin",
    "Admin": "Admin",
    "Jr.Admin": "Jr.Admin",
    "Sr.Moderator": "Sr.Mod",
    "Moderator": "Mod",
    "Jr.Moderator": "Jr.Mod",
    "Sr.Supporter": "Sr.Sup",
    "Supporter": "Sup",
    "Test Supporter": "T.Sup",
}

# Historical prefix labels are accepted only when cleaning old nicknames. They are
# deliberately NOT aliases for active roles and cannot grant rank or permissions.
LEGACY_ROLE_PREFIXES: tuple[str, ...] = (
    "Inhaber", "Stv.Inh", "L.Dev", "Dev", "L.PL", "L.PK", "PK",
    "L.DK", "DK", "Stv.DK", "L.TL", "L.TA", "TA", "L.Supervisor",
    "Supervisor", "Stv.Supervisor", "L.AB", "HTA", "CM", "Stv.CM",
    "Stv.FV", "L.Admin", "L.Mod", "L.Sup", "Aushilfe", "L.Bau",
    "Stv.L.Bau", "Sr.Bau", "Bau", "T.Bau", "Team",
)


def _normalize_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _extract_role_label(role_name: object) -> str:
    """Remove BOCHUM branding/decorations while retaining the actual rank label."""
    original = unicodedata.normalize("NFKC", str(role_name or "")).strip()
    if not original:
        return ""
    separators = ("メ", "║", "✘", "✗", "⌯⌲", "||")
    found = [(original.rfind(separator), separator) for separator in separators if separator in original]
    label = original
    if found:
        position, separator = max(found, key=lambda item: item[0])
        label = original[position + len(separator):].strip()
    while label and unicodedata.category(label[0])[0] not in {"L", "N"}:
        label = label[1:].lstrip()
    while label and unicodedata.category(label[-1])[0] not in {"L", "N"} and label[-1] not in "+#":
        label = label[:-1].rstrip()
    return re.sub(r"\s+", " ", label).strip()


_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "Founder": ("Founder", "FND"),
    "Rechte Hand": ("Rechte Hand", "RH"),
    "Owner": ("Owner",),
    "Co.Owner": ("Co.Owner", "Co Owner"),
    "Projektleitung": ("Projektleitung", "PL"),
    "Stv.Projektleitung": ("Stv.Projektleitung", "Stv.-Projektleitung", "Stv.PL", "Stv PL", "Stellv. Projektleitung"),
    "Serverleitung": ("Serverleitung", "SL"),
    "Stv.Serverleitung": ("Stv.Serverleitung", "Stv.-Serverleitung", "Stv.SL", "Stv SL", "Stellv. Serverleitung"),
    "Management": ("Management", "MA"),
    "Stv.Management": ("Stv.Management", "Stv.-Management", "Stv.MA", "Stv MA", "Stellv. Management"),
    "Teamleitung": ("Teamleitung", "TL"),
    "Stv.Teamleitung": ("Stv.Teamleitung", "Stv.-Teamleitung", "Stv.TL", "Stv TL", "Stellv. Teamleitung"),
    "Ausbilder": ("Ausbilder", "AB"),
    "Fraktionsverwaltung": ("Fraktionsverwaltung", "FV"),
    "Eventleitung": ("Eventleitung", "EL"),
    "Sr.Admin": ("Sr.Admin", "Sr Admin", "SrAdmin"),
    "Admin": ("Admin",),
    "Jr.Admin": ("Jr.Admin", "Jr Admin", "JrAdmin"),
    "Sr.Moderator": ("Sr.Moderator", "Sr Moderator", "Sr.Mod", "Sr Mod", "SrMod"),
    "Moderator": ("Moderator", "Mod"),
    "Jr.Moderator": ("Jr.Moderator", "Jr Moderator", "Jr.Mod", "Jr Mod", "JrMod"),
    "Sr.Supporter": ("Sr.Supporter", "Sr Supporter", "Sr.Sup", "Sr Sup", "SrSup"),
    "Supporter": ("Supporter", "Sup"),
    "Test Supporter": ("Test Supporter", "Test.Supporter", "T.Sup", "T Sup", "TestSup"),
}

TEAM_ROLE_ALIASES: dict[str, str] = {
    _normalize_key(alias): canonical
    for canonical, aliases in _ROLE_ALIASES.items()
    for alias in aliases
}
_TEAM_ROLE_GROUP_BY_NAME = {
    role_name: group_key
    for group_key, _group_label, role_names in TEAM_ROLE_GROUPS
    for role_name in role_names
}
_TEAM_ROLE_PRIORITY = {role_name: index for index, role_name in enumerate(TEAM_ROLE_ORDER)}


def canonical_team_role_name(role_name: object) -> str | None:
    """Return the canonical current role name, or None for non-team/legacy roles."""
    return TEAM_ROLE_ALIASES.get(_normalize_key(_extract_role_label(role_name)))


def team_role_group(role_name: object) -> str | None:
    """Return fuehrungsebene/highteam/lowteam for a current role name."""
    canonical = canonical_team_role_name(role_name)
    return _TEAM_ROLE_GROUP_BY_NAME.get(canonical) if canonical else None


def team_role_priority(role_name: object) -> int | None:
    """Return current hierarchy priority (0 = Founder, larger = lower rank)."""
    canonical = canonical_team_role_name(role_name)
    return _TEAM_ROLE_PRIORITY.get(canonical) if canonical else None
