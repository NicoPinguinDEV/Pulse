"""Shared, pure rank-hierarchy rules for promote/demote (Slash command and Dashboard).

Audit finding DISC-1: ``/promote`` compared the *target role* only with the bot and the target's
current rank, never with the acting member's own rank, while the Dashboard blocked exactly that.
This module is the single place for the actor-side rules so every interface applies the same ones.

The functions are side-effect free (no Discord calls, no database access): they only inspect the
member/role objects they are handed, so a refusal can never leave a protected write behind.
"""

from __future__ import annotations


def _role_ids(member) -> set[int]:
    return {int(getattr(r, "id", 0) or 0) for r in (getattr(member, "roles", None) or ())}


def team_rank_index(member, ordered_team_role_ids) -> int:
    """Highest index (lowest -> highest rank) of a configured team role held by ``member``; -1 if none."""
    held = _role_ids(member)
    indexes = [i for i, rid in enumerate(ordered_team_role_ids or ()) if int(rid) in held]
    return max(indexes) if indexes else -1


def _is_administrator(member) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(getattr(perms, "administrator", False))


def _role_grants_administrator(role) -> bool:
    perms = getattr(role, "permissions", None)
    return bool(getattr(perms, "administrator", False))


def actor_rank_change_problem(
    *,
    actor,
    actor_is_admin: bool,
    target,
    role,
    ordered_team_role_ids,
    action: str,
) -> str:
    """Return a German refusal reason, or ``""`` when the actor may perform ``action`` on ``target``.

    ``action`` is ``"promote"`` or ``"demote"``; anything else is refused (deny by default).
    ``ordered_team_role_ids`` are the configured team roles ordered lowest -> highest.
    """
    if action not in {"promote", "demote"}:
        return "Unbekannte Rangaktion."
    if actor is None or target is None or role is None:
        return "Akteur, Zielmitglied oder Zielrolle fehlt."
    if getattr(actor, "id", None) == getattr(target, "id", None):
        return "Du kannst deinen eigenen Rang nicht ändern."
    if getattr(target, "bot", False):
        return "Bots können nicht befördert oder degradiert werden."
    if getattr(role, "managed", False) or (hasattr(role, "is_default") and role.is_default()):
        return "Bitte wähle eine verwaltbare Team-Rangrolle aus."
    if actor_is_admin:
        # Administrators are bounded only by Discord/bot hierarchy (checked by the caller).
        return ""

    # --- Non-administrator actors: strict hierarchy ---------------------------------------------
    if _is_administrator(target):
        return "Mitglieder mit Administratorrechten können nur von Administratoren geändert werden."
    if _role_grants_administrator(role):
        return "Rollen mit Administratorrechten können nur von Administratoren vergeben werden."

    ordered = [int(r) for r in (ordered_team_role_ids or ())]
    actor_idx = team_rank_index(actor, ordered)
    if actor_idx < 0:
        return "Du besitzt keinen Teamrang und kannst keine Rangwechsel durchführen."
    if team_rank_index(target, ordered) >= actor_idx:
        return "Das Zielmitglied hat einen gleichen oder höheren Teamrang als du."
    actor_top = getattr(getattr(actor, "top_role", None), "position", None)
    target_top = getattr(getattr(target, "top_role", None), "position", None)
    if actor_top is not None and target_top is not None and target_top >= actor_top:
        return "Zielmitglied ist nicht in deiner zulässigen Rollen-Hierarchie."

    role_id = int(getattr(role, "id", 0) or 0)
    if role_id not in ordered:
        return "Bitte wähle eine verwaltbare Team-Rangrolle aus."
    if action == "promote":
        if ordered.index(role_id) >= actor_idx:
            return "Du kannst nicht auf deinen eigenen Rang oder höher befördern."
        role_pos = getattr(role, "position", None)
        if role_pos is not None and actor_top is not None and role_pos >= actor_top:
            return "Die Zielrolle liegt auf oder über deiner höchsten Rolle."
    return ""
