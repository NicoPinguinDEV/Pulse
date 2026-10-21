"""Rollback of warning-derived temporary Discord roles, shared by both action surfaces."""
from __future__ import annotations

from collections.abc import Callable


async def revoke_temporary_warning_roles(
    guild,
    member,
    warning: dict,
    entry: dict,
    reason: str,
    is_expired: Callable[[dict], bool],
) -> bool:
    """Remove the warning's temporary roles unless another active warning still needs them.

    If any removal fails, keep the original restriction metadata for later retry.
    If another active warning exists, retain those metadata records so normal expiry
    cleanup can remove the restriction when the final warning expires.
    """
    consequences = warning.setdefault("consequences", {})
    temp_roles = consequences.get("temporary_roles", [])
    if not isinstance(temp_roles, list):
        temp_roles = []
    temp_remove_failed = False
    temp_roles_to_keep = []

    for restriction in temp_roles:
        if not isinstance(restriction, dict):
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)
            continue
        try:
            role_id = int(restriction.get("role_id", 0) or 0)
        except (TypeError, ValueError):
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)
            continue
        role = guild.get_role(role_id)
        if not role:
            continue

        other_active_warning = any(
            other is not warning
            and isinstance(other, dict)
            and other.get("active", True)
            and not other.get("revoked_at")
            and not is_expired(other)
            for other in entry.get("warns_list", [])
        )
        if other_active_warning:
            temp_roles_to_keep.append(restriction)
            continue

        try:
            if role in getattr(member, "roles", []):
                bot_member = getattr(guild, "me", None)
                if bot_member and (
                    not bot_member.guild_permissions.manage_roles
                    or role.position >= bot_member.top_role.position
                ):
                    temp_remove_failed = True
                    temp_roles_to_keep.append(restriction)
                    continue
                await member.remove_roles(role, reason=f"Warn zurückgezogen · {reason}")
        except Exception:
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)

    consequences["temporary_roles"] = temp_roles if temp_remove_failed else temp_roles_to_keep
    return not temp_remove_failed
