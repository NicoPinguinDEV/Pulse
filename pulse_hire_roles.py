"""Deterministic role bundles for manual BOCHUM RP team hiring.

The selected configured rank is assigned by the dashboard. This module returns only
the additional team-membership, position-level and four-tier group roles. Those
additional roles must never be added to the configured rank list used for permissions.
"""
from __future__ import annotations

import pulse_ids
from pulse_team_roles import canonical_team_role_name


_LEVEL_BY_ROLE = {
    "Founder": "owner_team",
    "Rechte Hand": "rechte_hand",
    "Owner": "owner_team",
    "Co.Owner": "owner_team",
    "Projektleitung": "projektleitung",
    "Stv.Projektleitung": "projektleitung",
    "Serverleitung": "serverleitung",
    "Stv.Serverleitung": "serverleitung",
    "Management": "management",
    "Stv.Management": "management",
    "Teamleitung": "teamleitung",
    "Stv.Teamleitung": "teamleitung",
    "Ausbilder": "ausbilder",
    "Fraktionsverwaltung": "fraktionsverwaltung",
    "Eventleitung": "eventleitung",
    "Sr.Admin": "admin",
    "Admin": "admin",
    "Jr.Admin": "admin",
    "Sr.Moderator": "moderation",
    "Moderator": "moderation",
    "Jr.Moderator": "moderation",
    "Sr.Supporter": "supporter",
    "Supporter": "supporter",
    "Test Supporter": "supporter",
}

_GROUP_BY_ROLE = {
    **dict.fromkeys(("Founder", "Rechte Hand", "Owner", "Co.Owner"), "fuehrungsebene"),
    **dict.fromkeys(("Projektleitung", "Stv.Projektleitung", "Serverleitung", "Stv.Serverleitung"), "leitungsebene"),
    **dict.fromkeys(
        ("Management", "Stv.Management", "Teamleitung", "Stv.Teamleitung", "Ausbilder",
         "Fraktionsverwaltung", "Eventleitung"),
        "high_team",
    ),
    **dict.fromkeys(
        ("Sr.Admin", "Admin", "Jr.Admin", "Sr.Moderator", "Moderator", "Jr.Moderator",
         "Sr.Supporter", "Supporter", "Test Supporter"),
        "low_team",
    ),
}


def manual_hire_role_ids(role_name: object) -> tuple[int, ...]:
    """Return (team-member, position-level, group-level) role IDs for an active rank.

    Unknown or retired role names fail closed with an empty tuple.
    """
    canonical = canonical_team_role_name(role_name)
    if canonical is None:
        return ()

    level_key = _LEVEL_BY_ROLE.get(canonical)
    group_key = _GROUP_BY_ROLE.get(canonical)
    level_id = pulse_ids.TEAM_HIRE_LEVEL_ROLE_IDS.get(level_key)
    group_id = pulse_ids.TEAM_HIRE_GROUP_ROLE_IDS.get(group_key)
    if not level_id or not group_id or not pulse_ids.TEAM_MEMBER_ROLE_ID:
        return ()

    return tuple(dict.fromkeys((
        int(pulse_ids.TEAM_MEMBER_ROLE_ID),
        int(level_id),
        int(group_id),
    )))
