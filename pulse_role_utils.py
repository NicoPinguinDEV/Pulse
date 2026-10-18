"""Pure helpers for the role manager page (grouping by the server's separator roles)."""
from __future__ import annotations

import re

_SEPARATOR = re.compile(r"━{2,}|^[「\[].+[」\]]$")


def is_separator(name: str) -> bool:
    """Discord 'divider' roles such as 「━━━━ Supporter-Ebene ━━━━」."""
    return bool(_SEPARATOR.search(str(name or "").strip()))


def separator_label(name: str) -> str:
    label = re.sub(r"[「」\[\]━─—]+", " ", str(name or "")).strip()
    return re.sub(r"\s{2,}", " ", label) or "Rollen"


def group_roles(roles: list[dict]) -> list[dict]:
    """Group roles (sorted high → low) by the divider role directly *below* them.

    Each role dict needs at least 'name' and 'position'. Returns
    [{"label": str, "roles": [...], "separator": role|None}], top to bottom.
    """
    ordered = sorted(roles, key=lambda r: -int(r.get("position", 0)))
    groups: list[dict] = []
    buffer: list[dict] = []
    for role in ordered:
        if is_separator(role.get("name", "")):
            groups.append({"label": separator_label(role["name"]), "roles": buffer, "separator": role})
            buffer = []
        else:
            buffer.append(role)
    if buffer:
        groups.append({"label": "Weitere Rollen", "roles": buffer, "separator": None})
    return groups


def rank_change(current_ids: set[int], rank_ids: list[int], target_id: int) -> tuple[set[int], set[int]]:
    """(add, remove) to set exactly one team rank: the target, removing all other ranks."""
    ranks = {int(x) for x in rank_ids}
    add = {int(target_id)} - set(current_ids)
    remove = (set(current_ids) & ranks) - {int(target_id)}
    return add, remove
