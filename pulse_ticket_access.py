"""Central, server-side ticket visibility check (audit AUTHZ-1 / AUTHZ-2).

The Dashboard only asked for the general ``can_manage_tickets`` permission and the guild, so a
supporter could read transcripts and act on tickets of confidential categories (Highteam, Führungsebene)
that Discord protects through channel permissions. Every Dashboard ticket read/write path now calls
``can_access_ticket`` / ``filter_tickets`` *after* the ticket has been loaded.

Rule (deny by default for confidential data):
* a missing ticket, a ticket without a guild binding, or one from another guild is not accessible;
* administrators/owner (``perms["is_admin"]``) may access every ticket (existing rights architecture);
* categories of the confidential groups (``highteam``, ``leadership``) or categories with explicitly
  configured responsible roles require the member to hold one of ``category_roles``; confidential
  categories without any configured role are administrator-only (never "everyone with the permission");
* all other categories keep the existing behaviour (the caller has already checked ``can_manage_tickets``).

Callers must answer a refusal exactly like a missing ticket so nothing is disclosed.
"""

from __future__ import annotations

CONFIDENTIAL_GROUPS = frozenset({"highteam", "leadership"})


def _category_info(category_key):
    """(group, restricted_role_ids or None) for a ticket category key; never raises."""
    try:
        from cogs.tickets import category_meta, ticket_category_definitions
    except Exception:  # noqa: BLE001 - without the ticket cog definitions fail closed below
        return None, None
    key = str(category_key or "")
    definitions = ticket_category_definitions()
    definition = definitions.get(key)
    if not isinstance(definition, dict):
        # A removed, misspelled or otherwise unknown category may have been
        # confidential before its configuration disappeared. Do not let
        # category_meta's display fallback ("team") turn it into a public ACL.
        return None, None
    explicit = definition.get("role_ids") if isinstance(definition.get("role_ids"), list) else []
    return category_meta(key)[4], [str(r) for r in explicit if str(r).isdigit()]


def _is_admin(ctx) -> bool:
    perms = getattr(ctx, "perms", None) or {}
    return bool(perms.get("is_admin"))


def _member_role_ids(ctx) -> set[str]:
    member = getattr(ctx, "member", None)
    return {str(getattr(r, "id", "")) for r in (getattr(member, "roles", None) or ())}


def can_access_ticket(ctx, ticket) -> bool:
    if not ticket or ctx is None:
        return False
    guild = getattr(ctx, "guild", None)
    if guild is None:
        return False
    # Never treat an absent guild ID as a wildcard. Legacy/corrupt rows must be
    # explicitly reconciled before they can be exposed through a guild-scoped view.
    ticket_guild = str(ticket.get("guild_id") or "").strip()
    if not ticket_guild or ticket_guild != str(guild.id):
        return False
    if _is_admin(ctx):
        return True
    group, explicit_roles = _category_info(ticket.get("category"))
    if group is None:
        return False  # category definitions unavailable -> fail closed
    confidential = group in CONFIDENTIAL_GROUPS
    if not confidential and not explicit_roles:
        return True
    try:
        from cogs.tickets import category_roles
        allowed = {str(r.id) for r in category_roles(guild, str(ticket.get("category") or ""))}
    except Exception:  # noqa: BLE001
        return False
    return bool(allowed & _member_role_ids(ctx))


def filter_tickets(ctx, tickets) -> list:
    """Only the tickets the viewer may see (list pages, searches, member sheets, exports)."""
    return [t for t in (tickets or []) if can_access_ticket(ctx, t)]
