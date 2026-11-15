"""Audit #120: one policy for "may this person do this action?" – for the bot and the dashboard alike.

What is already central: *which rights a person has* is computed in exactly one place
(``pulse_permissions.compute``, used by ``webserver.compute_perms`` for the web *and* the bot cogs).
What was not central: *which right an action needs*. That decision was repeated inline in many places, e.g.
``is_admin or can_moderate_kicks or can_moderate_bans`` in routes, cogs and ``pulse_case_registry``.

This module names those decisions once. An action maps to the rights that unlock it (any one is enough);
administrators always pass. Rules are fail-closed: unknown actions, a missing/invalid permission dict and
anything other than a literal ``True`` deny. It does not compute rights, does not widen any existing check
and is not wired into routes yet – existing checks keep working; tests pin that the policy equals them.
"""
from __future__ import annotations

import pulse_permissions as pp

# Derived rights that ``webserver.compute_perms`` / ``pulse_permissions.compute`` add besides ``ALL_KEYS``.
DERIVED_KEYS = ("can_manage_team_warnings", "can_manage_factions")
KNOWN_KEYS = frozenset(pp.ALL_KEYS) | frozenset(DERIVED_KEYS) | {"is_admin"}

# action -> (label, rights of which ONE is enough). Administrators pass every action.
ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "dashboard.view": ("Dashboard ansehen", ("can_view_dashboard",)),
    "analytics.view": ("Auswertungen ansehen", ("can_view_analytics",)),
    "case.view.BM": ("Bürger-Moderation einsehen", ("can_moderate_kicks", "can_moderate_bans")),
    "case.view.KI": ("KI-Moderationsfälle einsehen", ("can_moderate_kicks", "can_moderate_bans")),
    "case.view.TW": ("Teamwarnungen einsehen", ("can_warn",)),
    "case.view.TF": ("Team-/Spielerfälle einsehen", ("can_promote",)),
    "case.view.TK": ("Tickets über ihre Referenz öffnen", ("can_manage_tickets",)),
    "case.view.SM": ("Spielermeldungen über ihre Referenz öffnen", ("can_view_analytics",)),
    "moderation.review_automod": ("AutoMod-Treffer prüfen", ("can_moderate_kicks",)),
    "moderation.warn": ("Teamwarnung ausstellen", ("can_warn",)),
    "ticket.manage": ("Tickets verwalten", ("can_manage_tickets",)),
    "application.manage": ("Bewerbungen verwalten", ("can_manage_applications",)),
    "task.manage": ("Aufgaben verwalten", ("can_manage_tasks", "can_promote")),  # web rule; /taskdone (bot) lacked can_promote
    "announcement.manage": ("Ankündigungen verwalten", ("can_manage_announcements",)),
    "handover.manage": ("Übergaben verwalten", ("can_manage_handover",)),
    "promotion.manage": ("Beförderungen verwalten", ("can_manage_promotions",)),
    "training.manage": ("Schulungen verwalten", ("can_manage_training",)),
    "wiki.manage": ("Wiki verwalten", ("can_manage_wiki",)),
    "server.manage": ("Server Control Center", ("can_manage_server",)),
    "admin.area": ("Administrationsbereich", ()),  # administrators only
}
CASE_KINDS = ("BM", "KI", "TW", "TF")
REFERENCE_KINDS = ("TK", "SM")  # #116: only resolvable by reference, not part of a person's moderation history


class PermissionDenied(PermissionError):
    """Raised by :func:`require`; the message names the action, never the person's rights."""


def _is_admin(perms) -> bool:
    return isinstance(perms, dict) and perms.get("is_admin") is True


def can(perms, action) -> bool:
    """True only if ``perms`` is a permission dict that unlocks ``action``. Everything else denies."""
    rule = ACTIONS.get(action) if isinstance(action, str) else None
    if rule is None or not isinstance(perms, dict):
        return False
    if _is_admin(perms):
        return True
    return any(perms.get(key) is True for key in rule[1])


def require(perms, action) -> None:
    if not can(perms, action):
        label = ACTIONS.get(action, (str(action)[:60],))[0] if isinstance(action, str) else "unbekannte Aktion"
        raise PermissionDenied(f"Dafür fehlt dir die Berechtigung: {label}.")


def allowed_actions(perms) -> list[str]:
    return [action for action in ACTIONS if can(perms, action)]


def allowed_case_kinds(perms, include_reference_only: bool = False) -> set[str]:
    """Case kinds the person may see – the policy form of ``pulse_case_registry.allowed_kinds``."""
    kinds = CASE_KINDS + REFERENCE_KINDS if include_reference_only else CASE_KINDS
    return {kind for kind in kinds if can(perms, f"case.view.{kind}")}


def explain(action) -> dict:
    """For "Wer darf was?": label and the rights that unlock an action (administrators always do)."""
    if not isinstance(action, str) or action not in ACTIONS:
        raise KeyError(action)
    label, keys = ACTIONS[action]
    return {"action": action, "label": label, "any_of": list(keys), "admin": True}
