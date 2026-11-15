"""Audit #123 (F8): ticket quick replies read from the central template library, with the legacy store as fallback.

No second template management: the ticket tools page stays the editing surface and writes the legacy store
(``pulse_ticket_extras``) exactly as before; :func:`sync_from_legacy` mirrors every save into the library as a new
*version* (history, archive and audit of ``pulse_templates``). The ``/antwort`` command and its autocomplete read
through :func:`offers`/:func:`resolve`:

* a library template wins over a legacy reply with the same scope and title; this holds for *archived* library
  templates too, so an archived text is never resurrected from the legacy store (exception: a template archived
  by :func:`sync_from_legacy` because the admin's newer legacy text breaks the placeholder rules - then the legacy
  text answers, verbatim, instead of an outdated version);
* legacy replies that are not in the library yet are still offered unchanged (transition), and are sent verbatim
  as before - without placeholders, exactly the old semantics;
* library texts are rendered with the placeholders ``{user}`` (ticket creator's name, never a mention),
  ``{ticket}``, ``{category}`` and ``{staff}``; mass mentions and role pings in the text or in inserted values are
  neutralised;
* the right is the one the command already needed (``ticket.manage`` = ``can_manage_tickets`` or admin); without it
  nothing is offered;
* a library problem (database error) never blocks a reply: it is logged and the legacy store answers;
* the version used is reported back and written as a ``ticket.quick_reply_sent`` event linked to the ticket.

Nothing is migrated automatically; the library fills up when an admin saves the replies or ``import_legacy`` is run.
"""
from __future__ import annotations

import logging

import pulse_events as events
import pulse_policy as policy
import pulse_templates as templates
import pulse_ticket_extras as tx

log = logging.getLogger("pulse.ticket_replies")

KIND = "ticket_reply"
MAX_OFFERS = tx.MAX_REPLIES


def _scopes(category) -> list[str]:
    cat = str(category or "")[:60]
    return [cat, "*"] if cat and cat != "*" else ["*"]


def _legacy(category) -> list[dict]:
    return [{"title": r["title"], "text": r["text"], "source": "legacy", "template_id": "", "version": 0,
             "scope": scope}
            for scope in _scopes(category) for r in tx.load_replies().get(scope, [])]


def offers(perms, guild_id, category) -> list[dict]:
    """Replies the person may send in a ticket of ``category``: ``[{title, source, template_id, version, scope}]``."""
    if not policy.can(perms, "ticket.manage"):
        return []
    scopes = _scopes(category)
    library: list[dict] = []
    shadowed: set[tuple[str, str]] = set()
    try:
        for row in templates.lookup(perms, guild_id, KIND, scopes, include_archived=True):
            if not (row["archived_at"] and row["archived_by_id"] == templates.TAKEOVER_ID):
                shadowed.add((row["scope"], row["title"].casefold()))  # archived by a person: never resurrect from legacy
            if not row["archived_at"]:
                library.append({"title": row["title"], "text": row["body"], "source": "library", "template_id": row["id"],
                                "version": int(row["current_version"]), "scope": row["scope"]})
    except Exception:  # noqa: BLE001 - a broken library must never block a reply
        log.warning("Vorlagenbibliothek nicht lesbar, Schnellantworten kommen aus dem alten Speicher", exc_info=True)
        library, shadowed = [], set()
    legacy = [r for r in _legacy(category) if (r["scope"], r["title"].casefold()) not in shadowed]
    order = {scope: i for i, scope in enumerate(scopes)}
    merged = sorted(library, key=lambda r: (order.get(r["scope"], 9), r["title"].casefold())) + legacy
    seen, out = set(), []
    for row in merged:  # a title offered twice (category and "*") keeps the more specific scope
        key = row["title"].casefold()
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out[:MAX_OFFERS]


def resolve(perms, guild_id, category, title, values=None) -> dict | None:
    """The reply with exactly this title as ``{text, title, source, template_id, version}``; ``None`` if there is none.

    Library texts are rendered (placeholders, mention protection); legacy texts are returned verbatim."""
    chosen = next((r for r in offers(perms, guild_id, category) if r["title"] == title), None)
    if chosen is None:
        return None
    if chosen["source"] == "legacy":
        return {"text": chosen["text"], "title": chosen["title"], "source": "legacy", "template_id": "", "version": 0}
    try:
        rendered = templates.render(perms, guild_id, chosen["template_id"], values or {})
    except Exception:  # noqa: BLE001 - e.g. archived in the meantime: treat as missing, controlled
        log.warning("Vorlage %s nicht renderbar", chosen["template_id"], exc_info=True)
        return None
    return {"text": rendered["text"], "title": chosen["title"], "source": "library",
            "template_id": rendered["template_id"], "version": rendered["version"]}


def record_use(ticket, actor_id, actor_name, reply: dict) -> None:
    """Contract event: which template version was sent in which ticket (never raises)."""
    events.emit_safe("ticket", "quick_reply_sent", target_type="ticket", target_id=ticket["id"], actor_id=actor_id,
                     actor_name=actor_name, correlation_id=events.correlation_for("ticket", ticket["id"]),
                     payload={"title": reply["title"], "source": reply["source"], "template_id": reply["template_id"],
                              "version": reply["version"]})


def sync_from_legacy(perms, guild_id, actor_id, actor_name, legacy=None) -> dict:
    """Mirror the (just saved) legacy replies into the library; never raises, returns the sync result or
    ``{"error": ...}`` so a library problem cannot make saving the ticket tools page fail."""
    try:
        legacy = tx.load_replies() if legacy is None else legacy
        entries = [(scope, r["title"], r["text"]) for scope, items in legacy.items() for r in items]
        return templates.sync_kind(perms, guild_id, KIND, entries, actor_id, actor_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("Schnellantworten konnten nicht in die Vorlagenbibliothek gespiegelt werden", exc_info=True)
        return {"error": type(exc).__name__}
