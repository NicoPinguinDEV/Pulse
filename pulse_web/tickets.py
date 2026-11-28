"""Team activity APIs, ticket configuration/workflows, moderation cases and checklists."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

@app.get("/api/team/activity")
async def team_activity_api(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    activity = get_activity_today(ctx.guild.id)
    role_ids = ctx.config.get("team_role_ids", [])
    members = [
        m for m in ctx.guild.members
        if not m.bot and any(r.id in role_ids for r in m.roles)
    ]
    current_ids = {m.id for m in members}
    eligible = activity.get("eligible")
    statuses = {}
    for m in members:
        if not activity["check_id"]:
            statuses[str(m.id)] = "none"
        elif eligible is not None and m.id not in eligible:
            statuses[str(m.id)] = "not_in_snapshot"
        else:
            statuses[str(m.id)] = "confirmed" if m.id in activity["confirmed"] else "open"

    confirmed = sum(1 for m in members if statuses.get(str(m.id)) == "confirmed")
    eligible_ids = eligible if eligible is not None else current_ids
    open_count = (
        sum(1 for uid in eligible_ids if uid in current_ids and uid not in activity["confirmed"])
        if activity["check_id"] else 0
    )
    return JSONResponse({
        "ok": True,
        "check_id": activity["check_id"],
        "date": activity["check_date"],
        "confirmed": confirmed,
        "open": open_count,
        "team_total": len(members),
        "snapshot_total": len(eligible) if eligible is not None else len(members),
        "statuses": statuses,
    })

@app.get("/api/team/warn-roles")
async def warn_roles_api(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_warn")
    rows = warning_role_health(ctx.guild, ctx.config)
    return JSONResponse({
        "ok": all(x["ok"] for x in rows),
        "sync_enabled": SYNC_WARN_ROLES,
        "roles": [
            {"level": x["level"], "id": x["id"], "name": x["role"].name if x["role"] else None,
             "ok": x["ok"], "detail": x["detail"]}
            for x in rows
        ],
    })


# =============================================================
# PULSE-EIGENES TICKETSYSTEM (Dashboard)
# =============================================================
# =============================================================
# PULSE-EIGENES TICKETSYSTEM (Dashboard)
# =============================================================
PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS = _pulse_ids.TICKET_PANEL_CHANNEL_IDS
PULSE_TICKET_OPEN_LOG_CHANNEL_ID = _pulse_ids.TICKET_OPEN_LOG_CHANNEL_ID
PULSE_TICKET_CLAIM_LOG_CHANNEL_ID = _pulse_ids.TICKET_CLAIM_LOG_CHANNEL_ID
PULSE_TICKET_META = {
    "support": ("Allgemeiner Support", "🎫"),
    "bug": ("Bug melden", "⚙️"),
    "unban": ("Entbannungsantrag", "📩"),
    "highteam": ("Highteam Ticket", "💢"),
    "leadership": ("Führungsebene Ticket", "🐬"),
}
PULSE_TICKET_PRIORITY_LABELS = {
    "low": "⚪ Niedrig",
    "normal": "🟢 Normal",
    "high": "🟠 Hoch",
    "urgent": "🚨 Dringend",
}
PULSE_TICKET_PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def pulse_ticket_config() -> dict:
    config = pulse_db.get_setting("ticket_system_config", {}) or {}
    return config if isinstance(config, dict) else {}


def pulse_ticket_category_registry() -> dict:
    """Return built-in plus configurable custom Pulse ticket categories."""
    registry = {
        "support": {"id": "support", "name": "Allgemeiner Support", "emoji": "🎫", "description": "Fragen, Hilfe und allgemeine Anliegen", "prefix": "support", "max_open": 10, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "bug": {"id": "bug", "name": "Bug melden", "emoji": "⚙️", "description": "Technische Fehler oder Server-Bugs melden", "prefix": "bug", "max_open": 5, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "unban": {"id": "unban", "name": "Entbannungsantrag", "emoji": "📩", "description": "Antrag auf Überprüfung einer Sanktion", "prefix": "unban", "max_open": 5, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "highteam": {"id": "highteam", "name": "Highteam Ticket", "emoji": "💢", "description": "Vertrauliche Angelegenheiten für das Highteam", "prefix": "highteam", "max_open": 3, "group": "highteam", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "leadership": {"id": "leadership", "name": "Führungsebene Ticket", "emoji": "🐬", "description": "Vertrauliche Angelegenheiten für die Führungsebene", "prefix": "leadership", "max_open": 3, "group": "leadership", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
    }
    saved = pulse_ticket_config().get("ticket_categories", [])
    for item in saved if isinstance(saved, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if not key or not re.fullmatch(r"[a-z0-9_-]{1,80}", key):
            continue
        if key not in registry and not key.startswith("custom_"):
            continue
        base = registry.get(key, {"id": key, "name": "", "emoji": "🎫", "description": "", "prefix": key, "max_open": 0, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": False})
        base.update(item)
        base["id"] = key
        base["builtin"] = key in {"support", "bug", "unban", "highteam", "leadership"}
        registry[key] = base
    return registry


def pulse_ticket_panel_channel_ids() -> tuple[int, ...]:
    raw = pulse_ticket_config().get("panel_channel_ids", list(PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS))
    if not isinstance(raw, list):
        raw = list(PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS)
    ids = []
    for value in raw[:25]:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id > 0 and channel_id not in ids:
            ids.append(channel_id)
    return tuple(ids) or PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS


def pulse_ticket_log_channel_id(kind: str) -> int:
    config = pulse_ticket_config()
    key = "ticket_open_log_channel_id" if kind == "created" else "ticket_claim_log_channel_id"
    fallback = PULSE_TICKET_OPEN_LOG_CHANNEL_ID if kind == "created" else PULSE_TICKET_CLAIM_LOG_CHANNEL_ID
    try:
        return int(config.get(key) or fallback)
    except (TypeError, ValueError):
        return fallback


def _ticket_dashboard_embed(ticket: dict, actor, kind: str, guild) -> discord.Embed:
    label, emoji = PULSE_TICKET_META.get(str(ticket.get("category")), ("Ticket", "🎫"))
    titles = {
        "created": "🎫 Neues Ticket geöffnet",
        "claimed": "📥 Ticket übernommen",
        "unclaimed": "↩️ Ticket freigegeben",
        "closed": "🔒 Ticket geschlossen",
        "reopened": "🔓 Ticket erneut geöffnet",
        "priority": "🚦 Ticket-Priorität geändert",
    }
    descriptions = {
        "created": "Ein neues Ticket wurde über Pulse erstellt.",
        "claimed": "Ein Teammitglied hat ein Pulse-Ticket beansprucht.",
        "unclaimed": "Ein beanspruchtes Ticket wurde freigegeben.",
        "closed": "Ein Pulse-Ticket wurde geschlossen.",
        "reopened": "Ein zuvor geschlossenes Ticket wurde erneut geöffnet.",
        "priority": "Die Priorität eines Pulse-Tickets wurde geändert.",
    }
    channel_id = str(ticket.get("channel_id") or "")
    is_claimed = bool(ticket.get("claimed_by_id"))
    embed = discord.Embed(
        title=titles.get(kind, "🎫 Pulse-Ticket aktualisiert"),
        description=descriptions.get(kind, "Ein Pulse-Ticket wurde aktualisiert."),
        color=_pulse_color("success") if kind in {"created", "reopened"} else (
            _pulse_color("danger") if kind == "closed" else (
                _pulse_color("warning") if kind == "priority" else _pulse_color("info")
            )
        ),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ticket-ID", value=str(ticket.get("id") or "unbekannt")[:100], inline=True)
    embed.add_field(name="Kategorie", value=f"{emoji} {label}", inline=True)
    embed.add_field(name="Status", value="🔒 Geschlossen" if ticket.get("status") == "closed" else ("🔵 In Bearbeitung" if is_claimed else "🟢 Offen"), inline=True)
    embed.add_field(name="Priorität", value=PULSE_TICKET_PRIORITY_LABELS.get(str(ticket.get("priority") or "normal"), "🟢 Normal"), inline=True)
    creator = str(ticket.get("user_name") or "Unbekannt")[:200]
    creator_id = str(ticket.get("user_id") or "")
    embed.add_field(name="Ersteller", value=creator + (f" ({creator_id})" if creator_id else ""), inline=True)
    if ticket.get("claimed_by_name"):
        embed.add_field(name="Aktueller Bearbeiter", value=str(ticket.get("claimed_by_name"))[:200], inline=True)
    if actor:
        embed.add_field(name="Aktion von", value=f"{actor.display_name} ({actor.id})", inline=True)
    if ticket.get("rating"):
        embed.add_field(name="Bewertung", value=f"{ticket['rating']}/5", inline=True)
    if ticket.get("close_reason") and ticket.get("status") == "closed":
        embed.add_field(name="Schließgrund", value=str(ticket.get("close_reason"))[:900], inline=False)
    if channel_id.isdigit():
        embed.add_field(name="Ticket-Kanal", value=f"<#{channel_id}>", inline=True)
        embed.url = f"https://discord.com/channels/{guild.id}/{channel_id}"
    embed.set_footer(text="Pulse Ticket-System • Ereignisprotokoll")
    return embed


async def _send_pulse_ticket_log(bot, guild, kind: str, ticket: dict, actor=None):
    if kind not in {"created", "reopened", "claimed"}:
        return False
    destination_id = pulse_ticket_log_channel_id("created" if kind in {"created", "reopened"} else "claimed")
    destination = guild.get_channel(destination_id)
    if destination is None:
        try:
            destination = await bot.fetch_channel(destination_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            destination = None
    if not isinstance(destination, discord.TextChannel) or destination.guild.id != guild.id:
        print(f"⚠️ Pulse-Tickets: Protokollkanal {destination_id} nicht erreichbar.")
        return False
    try:
        await destination.send(
            embed=_ticket_dashboard_embed(ticket, actor, kind, guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Tickets: Protokoll in {destination_id} fehlgeschlagen ({type(exc).__name__}).")
        return False


async def _refresh_pulse_ticket_discord_state(channel, ticket: dict):
    """Keep the ticket's Discord embed/button state aligned with dashboard actions."""
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        from cogs.tickets import move_ticket_channel, refresh_ticket_embed
        await refresh_ticket_embed(channel, ticket)
        if ticket.get("status") != "closed":
            await move_ticket_channel(channel, ticket, claimed=bool(ticket.get("claimed_by_id")))
    except Exception as exc:
        print(f"⚠️ Pulse-Tickets: Discord-Anzeige konnte nicht mit dem Dashboard synchronisiert werden ({type(exc).__name__}).")


def _ticket_channel_options(guild, selected_ids) -> str:
    selected = {int(item) for item in selected_ids if str(item).isdigit()}
    return "".join(
        f'<option value="{channel.id}" {"selected" if channel.id in selected else ""}>#{html.escape(channel.name)} · {channel.id}</option>'
        for channel in sorted(guild.text_channels, key=lambda item: item.position)
    )


def _ticket_role_options(guild, field_name: str, selected_ids) -> str:
    selected = {int(item) for item in selected_ids if str(item).isdigit()}
    roles = [role for role in guild.roles if not role.is_default() and not role.managed]
    return "".join(
        f'<option value="{role.id}" {"selected" if role.id in selected else ""}>{html.escape(role.name)} · {role.id}</option>'
        for role in sorted(roles, key=lambda item: item.position, reverse=True)
    )


@app.get("/tickets/panels", response_class=HTMLResponse)
@app.get("/tickets", response_class=HTMLResponse)
async def pulse_tickets_dashboard(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_manage_tickets")
    if request.url.path == "/tickets/panels" and not ctx.perms.get("is_admin"):
        return HTMLResponse(
            content="<!doctype html><html lang='de'><meta charset='utf-8'><title>Keine Berechtigung</title><body style='font-family:system-ui;background:#0b1020;color:#e8eefb;padding:40px'><h1>Kein Zugriff auf den Panel-Builder</h1><p>Nur Dashboard-Administratoren dürfen Ticket-Panels und Kategorien verwalten.</p><p><a style='color:#b9c7ff' href='/tickets'>Zur Ticket-Zentrale</a></p></body></html>",  # design-lint: standalone (eigenständige Fehlerseite ohne Dashboard-Shell)
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if request.url.path == "/tickets/panels":
        # Visual builder (live Discord preview, images, welcome texts, forms, automation).
        from pulse_ticket_builder import render_ticket_builder
        return render_ticket_builder(request, ctx)
    guild = ctx.guild
    settings = pulse_ticket_config()
    category_registry = pulse_ticket_category_registry()
    items = [
        item for item in pulse_db.list_tickets(limit=2000)
        if str(item.get("guild_id")) == str(guild.id)
    ]
    items.sort(key=lambda item: (
        PULSE_TICKET_PRIORITY_ORDER.get(str(item.get("priority") or "normal"), 2),
        str(item.get("opened_at") or ""),
    ), reverse=False)
    open_items = [item for item in items if item.get("status") != "closed" and not item.get("claimed_by_id")]
    claimed_items = [item for item in items if item.get("status") != "closed" and item.get("claimed_by_id")]
    closed_items = [item for item in items if item.get("status") == "closed"]
    urgent_count = sum(1 for item in items if item.get("status") != "closed" and item.get("priority") == "urgent")
    rated_count = sum(1 for item in items if item.get("rating"))

    filter_name = request.query_params.get("status", "all")
    if filter_name not in {"open", "claimed", "closed", "all", "rated"}:
        filter_name = "open"
    query = str(request.query_params.get("q", "")).strip()[:100].casefold()
    category_filter = str(request.query_params.get("category", "")).strip()
    groups = {
        "open": open_items,
        "claimed": claimed_items,
        "closed": closed_items,
        "all": items,
        "rated": [item for item in items if item.get("rating")],
    }
    selected_items = groups[filter_name]
    if category_filter in category_registry:
        selected_items = [item for item in selected_items if str(item.get("category")) == category_filter]
    if query:
        selected_items = [
            item for item in selected_items
            if query in str(item.get("id") or "").casefold()
            or query in str(item.get("user_name") or "").casefold()
            or query in str(item.get("claimed_by_name") or "").casefold()
            or query in str(item.get("category") or "").casefold()
            or query in str(item.get("close_reason") or "").casefold()
        ]
    selected_items = selected_items[:200]

    nav = "".join(
        f'<a class="tab {"active" if key == filter_name else ""}" href="/tickets?status={key}">{label}</a>'
        for key, label in (
            ("open", f"Offen ({len(open_items)})"),
            ("claimed", f"Übernommen ({len(claimed_items)})"),
            ("closed", f"Geschlossen ({len(closed_items)})"),
            ("rated", f"Bewertet ({rated_count})"),
            ("all", f"Alle ({len(items)})"),
        )
    )
    category_options = '<option value="">Alle Kategorien</option>' + "".join(
        f'<option value="{html.escape(key)}" {"selected" if category_filter == key else ""}>{html.escape(str(meta.get("emoji") or "🎫"))} {html.escape(str(meta.get("name") or "Ticket"))}</option>'
        for key, meta in category_registry.items()
    )
    cards = []
    for ticket in selected_items:
        ticket_id = str(ticket.get("id") or "")
        safe_ticket_id = html.escape(ticket_id)
        category_definition = category_registry.get(str(ticket.get("category")), {})
        category = str(category_definition.get("name") or "Ticket")
        emoji = str(category_definition.get("emoji") or "🎫")
        creator = html.escape(str(ticket.get("user_name") or "Unbekannt"))
        opened = html.escape(str(ticket.get("opened_at") or ""))
        channel_id = str(ticket.get("channel_id") or "")
        channel_link = (
            f'<a class="btn" href="https://discord.com/channels/{guild.id}/{channel_id}" target="_blank" rel="noopener">Discord öffnen ↗</a>'
            if channel_id.isdigit() else ""
        )
        closed = ticket.get("status") == "closed"
        is_claimed = bool(ticket.get("claimed_by_id"))
        priority = str(ticket.get("priority") or "normal")
        priority_label = PULSE_TICKET_PRIORITY_LABELS.get(priority, "🟢 Normal")
        status_html = (
            '<span class="state closed">Geschlossen</span>' if closed else
            ('<span class="state claimed">In Bearbeitung</span>' if is_claimed else '<span class="state open">Offen</span>')
        )
        actions = channel_link
        if closed:
            reason_html = html.escape(str(ticket.get("close_reason") or "Kein Schließgrund hinterlegt"))
            actions += (
                f'<a class="btn" href="/tickets/transcript/{safe_ticket_id}">Transcript ansehen</a>'
                if ticket.get("transcript_path") else ""
            )
            actions += (
                f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                '<input type="hidden" name="action" value="reopen"><button class="btn primary" type="submit">Wieder öffnen</button></form>'
            )
            detail = f'<div class="detail">Schließgrund: {reason_html}</div>'
            if ticket.get("closed_by_name"):
                detail += f'<div class="muted">Geschlossen von: {html.escape(str(ticket.get("closed_by_name")))}</div>'
        else:
            actions += (
                f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                '<input type="hidden" name="action" value="priority">'
                '<select name="priority" aria-label="Ticketpriorität">'
                + "".join(
                    f'<option value="{value}" {"selected" if priority == value else ""}>{label}</option>'
                    for value, label in PULSE_TICKET_PRIORITY_LABELS.items()
                )
                + '</select><button class="btn" type="submit">Priorität speichern</button></form>'
            )
            if is_claimed:
                if str(ticket.get("claimed_by_id")) == str(ctx.member.id) or ctx.perms.get("is_admin"):
                    actions += (
                        f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                        '<input type="hidden" name="action" value="unclaim"><button class="btn" type="submit">Freigeben</button></form>'
                    )
            else:
                actions += (
                    f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                    '<input type="hidden" name="action" value="claim"><button class="btn primary" type="submit">Übernehmen</button></form>'
                )
            actions += (
                f'<form method="post" action="/tickets/action" class="close-form"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                '<input type="hidden" name="action" value="close"><input name="reason" required maxlength="500" placeholder="Schließgrund">'
                '<button class="btn danger" type="submit">Schließen</button></form>'
            )
            detail = (
                f'<div class="detail">Bearbeiter: {html.escape(str(ticket.get("claimed_by_name") or "Niemand"))} · Priorität: {html.escape(priority_label)}</div>'
            )
        rating_html = (
            f'<div class="muted">Bewertung: {"⭐" * int(ticket.get("rating") or 0)} ({int(ticket.get("rating") or 0)}/5)'
            + (f' · {html.escape(str(ticket.get("rating_comment") or ""))}' if ticket.get("rating_comment") else "")
            + '</div>'
            if ticket.get("rating") else ""
        )
        cards.append(
            '<article class="ticket"><div class="ticket-main"><div class="kicker">'
            + emoji + " " + html.escape(category) + " · <code>" + safe_ticket_id + "</code> · "
            + html.escape(priority_label) + "</div><h3>" + creator + "</h3>"
            + '<div class="muted">Eröffnet: ' + opened + "</div>"
            + detail + rating_html + '</div><div class="ticket-side">' + status_html
            + '<div class="actions">' + actions + "</div></div></article>"
        )
    listing = "".join(cards) or '<div class="empty">Keine Tickets entsprechen dieser Auswahl.</div>'
    message = html.escape(str(ctx.msg or ""))
    notice = f'<div class="notice {"ok" if ctx.msg_ok else "err"}">{message}</div>' if message else ""

    panel_ids = pulse_ticket_panel_channel_ids()
    panel_input = ", ".join(str(item) for item in panel_ids)
    log_open_id = pulse_ticket_log_channel_id("created")
    log_claim_id = pulse_ticket_log_channel_id("claimed")
    settings_html = ""
    panel_builder_html = (
        '<section class="panel-builder settings" id="panel-builder">'
        '<div class="builder-heading"><div><h2>🎛️ Panel-Builder</h2>'
        '<p>Der Panel-Builder ist nur für Dashboard-Administratoren verfügbar.</p></div></div></section>'
    )
    if ctx.perms.get("is_admin"):
        role_cfg = settings
        team_selected = role_cfg.get("team_role_ids", ctx.config.get("team_role_ids", []))
        highteam_selected = role_cfg.get("highteam_role_ids", [])
        leadership_selected = role_cfg.get("leadership_role_ids", [])
        panel_options = "".join(
            f'<option value="{channel.id}" {"selected" if channel.id in panel_ids else ""}>#{html.escape(channel.name)} · {channel.id}</option>'
            for channel in sorted(guild.text_channels, key=lambda item: item.position)
        )
        channel_options_open = _ticket_channel_options(guild, [log_open_id])
        channel_options_claim = _ticket_channel_options(guild, [log_claim_id])
        team_options = _ticket_role_options(guild, "team_role_ids", team_selected)
        highteam_options = _ticket_role_options(guild, "highteam_role_ids", highteam_selected)
        leadership_options = _ticket_role_options(guild, "leadership_role_ids", leadership_selected)

        # Multi-panel designer: every panel can have its own Discord channel, embed and categories.
        panel_configs = settings.get("ticket_panels", [])
        panel_configs = [p for p in panel_configs if isinstance(p, dict)] if isinstance(panel_configs, list) else []
        text_channels = sorted(guild.text_channels, key=lambda item: item.position)
        role_list = [role for role in guild.roles if not role.is_default() and not role.managed]
        category_channels = sorted(guild.categories, key=lambda item: item.position)
        panel_cards = []
        active_categories = [(key, meta) for key, meta in category_registry.items() if meta.get("active", True)]

        for panel in panel_configs:
            pid = str(panel.get("id") or "")
            safe_pid = html.escape(pid, quote=True)
            selected_keys = {str(value) for value in panel.get("category_keys", []) if str(value) in category_registry}
            channel_id = str(panel.get("channel_id") or "")
            channel_options_for_panel = '<option value="">Kanal auswählen …</option>' + "".join(
                f'<option value="{ch.id}" {"selected" if str(ch.id) == channel_id else ""}>#{html.escape(ch.name)} · {ch.id}</option>'
                for ch in text_channels
            )
            category_checks = "".join(
                f'<label class="check"><input type="checkbox" name="category_keys" value="{html.escape(key, quote=True)}" {"checked" if key in selected_keys else ""}><span>{html.escape(str(meta.get("emoji") or "🎫"))} <strong>{html.escape(str(meta.get("name") or key))}</strong><small>{html.escape(str(meta.get("description") or ""))}</small></span></label>'
                for key, meta in active_categories
            )
            color_value = str(panel.get("color") or "#5865F2")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color_value):
                color_value = "#5865F2"
            state_label = "✅ Veröffentlicht" if panel.get("message_id") else "📝 Noch nicht veröffentlicht"
            panel_cards.append(f"""<details class="builder-item"><summary><span>{html.escape(str(panel.get("name") or "Panel"))}</span><small>{state_label}</small></summary>
<form method="post" action="/tickets/panels/save" class="builder-form">
<input type="hidden" name="panel_id" value="{safe_pid}">
<label>Interner Panelname<input name="name" value="{html.escape(str(panel.get("name") or ""), quote=True)}" maxlength="80" required></label>
<label>Panel-Kanal<select name="channel_id" required>{channel_options_for_panel}</select></label>
<label>Embed-Titel<input name="title" value="{html.escape(str(panel.get("title") or panel.get("name") or "Ticket-Support"), quote=True)}" maxlength="256" required></label>
<label>Embed-Farbe<input type="color" name="color" value="{color_value}" required></label>
<label class="wide">Embed-Beschreibung<textarea name="description" maxlength="4000" rows="3" required>{html.escape(str(panel.get("description") or ""))}</textarea></label>
<label class="wide">Fußzeile<input name="footer" value="{html.escape(str(panel.get("footer") or "Pulse Ticket-System"), quote=True)}" maxlength="200"></label>
<fieldset class="wide"><legend>Kategorien in diesem Panel (bis zu 25)</legend><div class="check-grid">{category_checks}</div></fieldset>
<div class="wide actions"><button class="btn primary" type="submit">Änderungen speichern</button></div>
</form>
<div class="actions panel-actions">
<form method="post" action="/tickets/panels/publish"><input type="hidden" name="panel_id" value="{safe_pid}"><button class="btn success" type="submit">🚀 Panel veröffentlichen / aktualisieren</button></form>
<form method="post" action="/tickets/panels/delete" onsubmit="return confirm('Dieses Panel wirklich löschen? Die registrierte Panel-Nachricht wird ebenfalls entfernt.');"><input type="hidden" name="panel_id" value="{safe_pid}"><button class="btn danger" type="submit">Panel löschen</button></form>
</div></details>""")
        panel_cards_html = "".join(panel_cards) or '<div class="empty small-empty">Du hast noch keine eigenen Panels erstellt. Erstelle dein erstes Panel rechts.</div>'

        category_editor_cards = []
        for key, meta in category_registry.items():
            category_id_html = html.escape(key, quote=True)
            selected_roles_raw = meta.get("role_ids") or settings.get(str(meta.get("group") or "team") + "_role_ids", ctx.config.get("team_role_ids", []))
            if not isinstance(selected_roles_raw, (list, tuple, set)):
                selected_roles_raw = []
            selected_role_ids = {str(value) for value in selected_roles_raw if str(value).isdigit()}
            parent_id = str(meta.get("parent_category_id") or "")
            parent_options = '<option value="">Standard-Ticketkategorie verwenden</option>' + "".join(
                f'<option value="{cat.id}" {"selected" if str(cat.id) == parent_id else ""}>📁 {html.escape(cat.name)} · {cat.id}</option>'
                for cat in category_channels
            )
            role_options_for_category = "".join(
                f'<option value="{role.id}" {"selected" if str(role.id) in selected_role_ids else ""}>{html.escape(role.name)} · {role.id}</option>'
                for role in sorted(role_list, key=lambda item: item.position, reverse=True)
            )
            group_options = "".join(
                f'<option value="{value}" {"selected" if str(meta.get("group") or "team") == value else ""}>{label}</option>'
                for value, label in (("team", "Support-Team"), ("highteam", "Highteam"), ("leadership", "Führungsebene"))
            )
            delete_form_html = ""
            if not meta.get("builtin"):
                delete_form_html = f"""<form method="post" action="/tickets/categories/delete" class="category-delete" onsubmit="return confirm('Diese eigene Kategorie wirklich löschen?');"><input type="hidden" name="category_id" value="{category_id_html}"><button class="btn danger" type="submit">Eigene Kategorie löschen</button></form>"""
            category_editor_cards.append(f"""<details class="builder-item category-item"><summary><span>{html.escape(str(meta.get("emoji") or "🎫"))} {html.escape(str(meta.get("name") or key))}</span><small>{"Standardkategorie" if meta.get("builtin") else "Eigene Kategorie"}</small></summary>
<form method="post" action="/tickets/categories/save" class="builder-form">
<input type="hidden" name="category_id" value="{category_id_html}">
<label>Name<input name="name" value="{html.escape(str(meta.get("name") or ""), quote=True)}" maxlength="80" required></label>
<label>Präfix für Ticketkanäle<input name="prefix" value="{html.escape(str(meta.get("prefix") or key), quote=True)}" pattern="[a-zA-Z0-9_-]{{1,24}}" maxlength="24" required></label>
<label>Emoji<input name="emoji" value="{html.escape(str(meta.get("emoji") or "🎫"), quote=True)}" maxlength="100" required></label>
<label>Maximal offene Tickets (0 = unbegrenzt)<input type="number" name="max_open" min="0" max="1000" value="{int(meta.get("max_open") or 0)}"></label>
<label class="wide">Beschreibung im Auswahlmenü<input name="description" value="{html.escape(str(meta.get("description") or ""), quote=True)}" maxlength="100" required></label>
<label>Discord-Zielkategorie<select name="parent_category_id">{parent_options}</select></label>
<label>Zuständige Rollen (Mehrfachauswahl)<select name="role_ids" multiple size="5">{role_options_for_category}</select></label>
<label class="wide">Begrüßung im neuen Ticket<textarea name="opening_message" maxlength="1800" rows="3">{html.escape(str(meta.get("opening_message") or ""))}</textarea></label>
<label>Bearbeitungsbereich<select name="group">{group_options}</select></label>
<div class="wide actions"><button class="btn primary" type="submit">Kategorie speichern</button></div>
</form>
{delete_form_html}
</details>""")
        category_editor_html = "".join(category_editor_cards)
        new_category_options_roles = "".join(
            f'<option value="{role.id}">{html.escape(role.name)} · {role.id}</option>'
            for role in sorted(role_list, key=lambda item: item.position, reverse=True)
        )
        new_parent_options = '<option value="">Standard-Ticketkategorie verwenden</option>' + "".join(
            f'<option value="{cat.id}">📁 {html.escape(cat.name)} · {cat.id}</option>'
            for cat in category_channels
        )
        default_category_keys = [key for key, meta in active_categories][:25]
        new_panel_checks = "".join(
            f'<label class="check"><input type="checkbox" name="category_keys" value="{html.escape(key, quote=True)}" {"checked" if key in default_category_keys else ""}><span>{html.escape(str(meta.get("emoji") or "🎫"))} <strong>{html.escape(str(meta.get("name") or key))}</strong><small>{html.escape(str(meta.get("description") or ""))}</small></span></label>'
            for key, meta in active_categories
        )
        panel_builder_html = f"""<section class="panel-builder settings" id="panel-builder">
<div class="builder-heading"><div><div class="muted">PULSE TICKET SYSTEM</div><h2>🎛️ Panel-Builder</h2><p>Erstelle beliebig viele separate Panels. Jedes Panel hat einen eigenen Kanal, eigenes Embed-Design und eine eigene Kategorienauswahl. Speichern und anschließend veröffentlichen.</p></div><span class="builder-count">{len(panel_configs)} Panels</span></div>
<div class="builder-columns"><div><h3>Deine Panels</h3><div class="builder-list">{panel_cards_html}</div></div>
<div><h3>➕ Neues Panel erstellen</h3><form method="post" action="/tickets/panels/save" class="builder-form">
<label>Interner Panelname<input name="name" placeholder="z. B. Support-Zentrale" maxlength="80" required></label>
<label>Panel-Kanal<select name="channel_id" required><option value="">Kanal auswählen …</option>{''.join(f'<option value="{ch.id}">#{html.escape(ch.name)} · {ch.id}</option>' for ch in text_channels)}</select></label>
<label>Embed-Titel<input name="title" value="Ticket-Support" maxlength="256" required></label>
<label>Embed-Farbe<input type="color" name="color" value="#5865F2" required></label>
<label class="wide">Embed-Beschreibung<textarea name="description" maxlength="4000" rows="4" required>Wähle unten die passende Kategorie aus. Pulse erstellt anschließend einen privaten Ticket-Kanal.</textarea></label>
<label class="wide">Fußzeile<input name="footer" value="Pulse Ticket-System" maxlength="200"></label>
<fieldset class="wide"><legend>Kategorien für dieses Panel (bis zu 25)</legend><div class="check-grid">{new_panel_checks}</div></fieldset>
<button class="btn primary wide" type="submit">Panel erstellen und speichern</button>
</form></div></div>
<hr>
<div class="builder-heading"><div><h3>📂 Kategorien verwalten</h3><p>Jede Kategorie kann ein eigenes Präfix, Emoji, Beschreibung, zuständige Rollen, Discord-Zielkategorie und einen Begrüßungstext besitzen. Füge Kategorien anschließend über die Checkboxen zu beliebigen Panels hinzu.</p></div></div>
<div class="category-editor-grid">{category_editor_html}
<details class="builder-item"><summary><span>➕ Neue Ticket-Kategorie anlegen</span><small>Benutzerdefiniert</small></summary>
<form method="post" action="/tickets/categories/save" class="builder-form">
<input type="hidden" name="category_id" value="">
<label>Name<input name="name" placeholder="z. B. Fraktionsverwaltung" maxlength="80" required></label>
<label>Präfix für Ticketkanäle<input name="prefix" placeholder="fraktion" pattern="[a-zA-Z0-9_-]{{1,24}}" maxlength="24" required></label>
<label>Emoji<input name="emoji" value="🎫" maxlength="100" required></label>
<label>Maximal offene Tickets (0 = unbegrenzt)<input type="number" name="max_open" min="0" max="1000" value="0"></label>
<label class="wide">Beschreibung im Auswahlmenü<input name="description" placeholder="Worum geht es in dieser Kategorie?" maxlength="100" required></label>
<label>Discord-Zielkategorie<select name="parent_category_id">{new_parent_options}</select></label>
<label>Zuständige Rollen (Mehrfachauswahl)<select name="role_ids" multiple size="5">{new_category_options_roles}</select></label>
<label class="wide">Begrüßung im neuen Ticket<textarea name="opening_message" maxlength="1800" rows="3" placeholder="Dieser Text wird im neuen Ticket angezeigt."></textarea></label>
<label>Bearbeitungsbereich<select name="group"><option value="team">Support-Team</option><option value="highteam">Highteam</option><option value="leadership">Führungsebene</option></select></label>
<button class="btn primary wide" type="submit">Kategorie erstellen</button>
</form></details>
</div></section>"""

        settings_html = f"""<section class="settings">
<details><summary>⚙ Ticket-System konfigurieren</summary>
<form method="post" action="/tickets/settings" class="settings-form">
<label>Panel-Kanäle (mehrere auswählen, für Mehrfachauswahl STRG halten)<select name="panel_channel_ids" multiple size="6" required>{panel_options}</select></label>
<label>Protokoll: neue Tickets<select name="open_log_channel_id" required>{channel_options_open}</select></label>
<label>Protokoll: beanspruchte Tickets<select name="claim_log_channel_id" required>{channel_options_claim}</select></label>
<label>Zuständige Rollen: Allgemeiner Support, Bug und Entbannungsantrag<select name="team_role_ids" multiple size="6">{team_options}</select></label>
<label>Zuständige Rollen: Highteam<select name="highteam_role_ids" multiple size="5">{highteam_options}</select></label>
<label>Zuständige Rollen: Führungsebene<select name="leadership_role_ids" multiple size="5">{leadership_options}</select></label>
<p class="muted">Die Rollenauswahl steuert Sichtbarkeit, Ping und Bearbeitungsrechte der jeweiligen Ticketkategorie. Wird für einen Bereich keine Rolle ausgewählt, greift bei allgemeinen Tickets die bestehende Pulse-Teamrollen-Konfiguration.</p>
<button class="btn primary" type="submit">Einstellungen speichern</button>
</form>
<form method="post" action="/tickets/setup-panels"><button class="btn" type="submit">Panels aktualisieren/einrichten</button></form>
</details></section>"""

    page = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pulse · Ticket-Zentrale</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0%,#1a2850 0,transparent 35%),#0b1020;color:#e8eefb;font-family:system-ui,Segoe UI,sans-serif}}a{{color:inherit;text-decoration:none}}
.wrap{{max-width:1280px;margin:auto;padding:28px 18px 56px}}header,.hero{{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;align-items:center}}.brand{{font-size:20px;font-weight:900}}.nav,.tabs,.actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
h1{{font-size:clamp(28px,4vw,40px);margin:22px 0 8px}}.muted{{color:#9cabc5;font-size:12px}}.btn,.tab{{border:1px solid #2a3952;border-radius:10px;padding:9px 12px;font-size:12px;font-weight:800;background:#172238;color:#e8eefb;cursor:pointer;display:inline-flex;align-items:center}}.btn.primary,.tab.active{{background:#647cf5;border-color:#647cf5;color:white}}.btn.danger{{background:#502034;border-color:#81394d}}
.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}}.stat,.ticket,.settings{{background:#121a2c;border:1px solid #27354d;border-radius:17px;padding:16px}}.stat span{{color:#9cabc5;font-size:12px}}.stat strong{{display:block;font-size:28px;margin-top:4px}}
.ticket-list{{display:grid;gap:10px;margin-top:14px}}.ticket{{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}.ticket-main{{flex:1;min-width:220px}}.kicker{{color:#b9c7ff;font-size:12px;font-weight:800;margin-bottom:8px}}h3{{margin:0 0 5px;font-size:17px}}.detail{{font-size:12px;color:#c4cde0;margin-top:10px;overflow-wrap:anywhere}}.ticket-side{{display:grid;justify-items:end;gap:10px;min-width:240px}}.actions form{{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:0}}.actions{{justify-content:flex-end;max-width:680px}}input,select{{max-width:100%;background:#0b1221;color:#e8eefb;border:1px solid #27354d;border-radius:9px;padding:9px;font-size:11px}}select[name="priority"]{{width:130px}}.close-form input[name="reason"]{{width:150px}}
.state{{font-size:10px;font-weight:900;border-radius:999px;padding:6px 9px}}.state.open{{background:#153b31;color:#9cf0d0}}.state.claimed{{background:#1c2c55;color:#bfcaff}}.state.closed{{background:#333b4c;color:#d9e0ed}}.empty{{text-align:center;padding:45px;color:#9cabc5;border:1px dashed #27354d;border-radius:17px}}.notice{{padding:12px;border-radius:12px;margin-top:14px;font-size:12px}}.notice.ok{{background:#123b30;color:#adf5d8}}.notice.err{{background:#4a1e2c;color:#ffd4dc}}footer{{margin-top:28px;color:#9cabc5;font-size:11px}}.filters{{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}}.filters input{{min-width:230px}}.filters select{{min-width:180px}}.settings{{margin-top:24px}}summary{{font-weight:900;cursor:pointer}}.settings-form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:18px}}.panel-builder{{padding:22px}}.builder-heading{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}.builder-heading h2{{margin:6px 0}}.panel-builder h3{{margin:16px 0 10px}}.builder-heading p{{max-width:900px;color:#9cabc5;font-size:13px}}.builder-count{{background:#202d4a;border:1px solid #34486f;padding:9px 13px;border-radius:999px;font-weight:800}}.builder-columns{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:14px}}.builder-list,.category-editor-grid{{display:grid;gap:10px}}.category-editor-grid{{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:12px}}.builder-item{{background:#0d1527;border:1px solid #2a3952;border-radius:12px;padding:13px;margin-bottom:10px}}.builder-item summary{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.builder-item summary small{{color:#9cabc5;font-size:10px}}.builder-form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px}}.builder-form label{{display:grid;gap:6px;font-size:12px;font-weight:750}}.builder-form input,.builder-form textarea,.builder-form select{{width:100%;font:inherit}}.builder-form textarea{{resize:vertical;background:#0b1221;color:#e8eefb;border:1px solid #27354d;border-radius:9px;padding:10px}}.builder-form .wide{{grid-column:1/-1}}.builder-form fieldset{{border:1px solid #2a3952;border-radius:10px;padding:12px;min-width:0}}.builder-form legend{{font-size:12px;font-weight:800;padding:0 6px}}.check-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.check{{display:flex!important;align-items:flex-start;gap:8px!important;padding:9px;background:#121a2c;border-radius:8px;font-weight:500!important}}.check input{{width:auto!important;margin-top:3px}}.check span{{display:grid;gap:3px}}.check small{{color:#9cabc5;font-weight:400}}.panel-actions{{justify-content:flex-start;margin-top:12px}}.category-delete{{margin-top:10px}}.btn.success{{background:#124534;border-color:#246c4e}}.small-empty{{padding:20px}}.settings-form label{{display:grid;gap:6px;font-size:12px;font-weight:800}}.settings-form select[multiple]{{width:100%;min-height:130px}}.settings-form p,.settings-form button{{grid-column:1/-1}}
@media(max-width:820px){{.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.settings-form{{grid-template-columns:1fr}}.builder-columns,.category-editor-grid{{grid-template-columns:1fr}}.ticket-side{{width:100%;justify-items:start}}.actions{{justify-content:flex-start}}}}
@media(max-width:480px){{.stats{{grid-template-columns:1fr}}.wrap{{padding:18px 12px 40px}}}}
</style>{SCROLL_RESTORE_SCRIPT}</head><body><main class="wrap">
<header><a class="brand" href="/dashboard">⚡ Pulse TeamOS</a><nav class="nav"><a class="btn" href="/dashboard">Dashboard</a><a class="btn" href="/team">Teamliste</a><a class="btn" href="/tickets">Ticket-Zentrale</a><a class="btn primary" href="/tickets/panels#panel-builder">Panel-Builder</a></nav></header>
<section class="hero"><div><div class="muted">BOCHUM RP · TEAM OPERATIONS · <strong>Pulse Release v{PULSE_VERSION}</strong></div><h1>Ticket-Zentrale</h1><div class="muted">Tickets priorisieren, übernehmen, wieder öffnen, schließen und nachhalten – direkt in Pulse.</div></div><a class="btn" href="https://discord.com/channels/{guild.id}/{log_open_id}" target="_blank" rel="noopener">Öffnungsprotokoll ↗</a></section>
{notice}
{panel_builder_html}
<section class="stats"><div class="stat"><span>Offen</span><strong>{len(open_items)}</strong></div><div class="stat"><span>🚨 Dringend</span><strong>{urgent_count}</strong></div><div class="stat"><span>◉ Übernommen</span><strong>{len(claimed_items)}</strong></div><div class="stat"><span>★ Bewertet</span><strong>{rated_count}</strong></div></section>
<nav class="tabs">{nav}</nav>
<form class="filters" method="get" action="/tickets"><input name="q" value="{html.escape(str(request.query_params.get('q', '')), quote=True)}" placeholder="Ticket-ID, Nutzer, Bearbeiter oder Grund suchen"><select name="category">{category_options}</select><input type="hidden" name="status" value="{html.escape(filter_name)}"><button class="btn primary" type="submit">Suchen / filtern</button><a class="btn" href="/tickets">Zurücksetzen</a></form>
<section class="ticket-list">{listing}</section>
{settings_html}
<footer>Pulse TeamOS · Native Pulse-Tickets · Ticketdaten, Aktionen und Protokolle bleiben im eigenen Pulse-System.</footer>
</main></body></html>"""
    if request.url.path == "/tickets/panels":
        # Render a dedicated builder page instead of merely scrolling within the ticket list.
        # Reuse the established stylesheet and the same admin form handlers.
        document_head = page.split("<body>", 1)[0] + "<body>"
        page = document_head + f"""
<main class="wrap">
<header>
  <a class="brand" href="/dashboard">⚡ Pulse TeamOS</a>
  <nav class="nav">
    <a class="btn" href="/dashboard">Dashboard</a>
    <a class="btn" href="/team">Teamliste</a>
    <a class="btn" href="/tickets">Ticket-Zentrale</a>
    <a class="btn primary" href="/tickets/panels">Panel-Builder</a>
  </nav>
</header>
<section class="hero">
  <div><div class="muted">BOCHUM RP · TEAM OPERATIONS · <strong>Pulse Release v{PULSE_VERSION}</strong></div>
  <h1>Ticket-Panel-Builder</h1>
  <div class="muted">Erstelle mehrere unabhängige Ticket-Panels und verwalte deren Kategorien direkt in Pulse.</div></div>
  <a class="btn" href="/tickets">← Zur Ticket-Zentrale</a>
</section>
{notice}
{panel_builder_html}
<footer>Pulse TeamOS · Ticket-Panel-Builder · Einstellungen werden im eigenen Pulse-System gespeichert.</footer>
</main></body></html>"""
    return HTMLResponse(content=page, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})


@app.post("/tickets/settings")
async def pulse_tickets_dashboard_settings(
    request: Request,
    panel_channel_ids: list[str] = Form(default=[]),
    open_log_channel_id: str = Form(...),
    claim_log_channel_id: str = Form(...),
    team_role_ids: list[str] = Form(default=[]),
    highteam_role_ids: list[str] = Form(default=[]),
    leadership_role_ids: list[str] = Form(default=[]),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    if not panel_channel_ids:
        return back("/tickets", "Bitte wähle mindestens einen Ticketpanel-Kanal aus.", False)
    if len(panel_channel_ids) > 25:
        return back("/tickets", "Es können höchstens 25 Panel-Kanäle ausgewählt werden.", False)

    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets", "Pulse-Bot ist nicht bereit.", False)

    valid_panels = []
    for raw in panel_channel_ids:
        try:
            channel_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets", "Ungültige Panel-Kanal-ID.", False)
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != ctx.guild.id:
            return back("/tickets", f"Panel-Kanal {channel_id} ist kein Textkanal dieses Servers.", False)
        if channel_id not in valid_panels:
            valid_panels.append(channel_id)

    log_ids = {}
    for key, raw in (("ticket_open_log_channel_id", open_log_channel_id), ("ticket_claim_log_channel_id", claim_log_channel_id)):
        try:
            channel_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets", "Ungültige Protokoll-Kanal-ID.", False)
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != ctx.guild.id:
            return back("/tickets", f"Protokoll-Kanal {channel_id} ist kein Textkanal dieses Servers.", False)
        log_ids[key] = channel_id

    role_values = {}
    for key, raw_values in (
        ("team_role_ids", team_role_ids),
        ("highteam_role_ids", highteam_role_ids),
        ("leadership_role_ids", leadership_role_ids),
    ):
        values = []
        for raw in raw_values:
            try:
                role_id = int(raw)
            except (TypeError, ValueError):
                return back("/tickets", "Ungültige Rollen-ID.", False)
            role = ctx.guild.get_role(role_id)
            if role is None or role.is_default() or role.managed:
                return back("/tickets", "Bitte wähle nur gültige, normale Serverrollen.", False)
            if role_id not in values:
                values.append(role_id)
        role_values[key] = values

    settings = pulse_ticket_config()
    settings.update(log_ids)
    settings.update(role_values)
    settings["panel_channel_ids"] = valid_panels
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets", f"Ticket-Einstellungen gespeichert: {len(valid_panels)} Panelkanal/-kanäle konfiguriert.", True)


@app.post("/tickets/setup-panels")
async def pulse_ticket_panels_setup(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, admin=True)
    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets", "Pulse-Bot ist nicht bereit.", False)
    cog = bot.get_cog("Tickets")
    if not cog or not hasattr(cog, "setup_panels_for_guild"):
        return back("/tickets", "Das Pulse-Ticketmodul ist nicht geladen.", False)
    try:
        result = await cog.setup_panels_for_guild(ctx.guild)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Ticketpanels über Dashboard konnten nicht eingerichtet werden ({type(exc).__name__}).")
        return back("/tickets", "Discord hat die Einrichtung abgelehnt. Prüfe die Rechte des Bots.", False)
    successes = len(result.get("successes", []))
    failures = result.get("failures", [])
    message = f"Pulse-Ticketpanels: {successes}/{result.get('expected', 6)} eingerichtet."
    if failures:
        message += " Nicht eingerichtet: " + "; ".join(failures)
    return back("/tickets", message, successes > 0 and not failures)



@app.post("/tickets/panels/save")
async def pulse_ticket_panel_save(
    request: Request,
    name: str = Form(...),
    channel_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    color: str = Form("#5865F2"),
    footer: str = Form("Pulse Ticket-System"),
    panel_id: str = Form(""),
    category_keys: list[str] = Form(default=[]),
    thumbnail_url: str = Form(""),
    banner_url: str = Form(""),
    use_server_icon: bool = Form(False),
    show_load: bool = Form(False),
    list_categories: bool = Form(False),
    component_style: str = Form("select"),
    placeholder: str = Form(""),
    welcome_title: str = Form(""),
    welcome_message: str = Form(""),
    ping_roles: bool = Form(False),
    remove_thumbnail: bool = Form(False),
    remove_banner: bool = Form(False),
    builder_v2: bool = Form(False),
    thumbnail_upload: UploadFile | None = File(None),
    banner_upload: UploadFile | None = File(None),
    welcome_thumbnail_url: str = Form(""),
    welcome_banner_url: str = Form(""),
    welcome_use_avatar: bool = Form(False),
    remove_welcome_thumbnail: bool = Form(False),
    remove_welcome_banner: bool = Form(False),
    welcome_thumbnail_upload: UploadFile | None = File(None),
    welcome_banner_upload: UploadFile | None = File(None),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets/panels", "Pulse-Bot ist nicht bereit. Panel wurde nicht gespeichert.", False)
    try:
        target_channel_id = int(channel_id)
    except (TypeError, ValueError):
        return back("/tickets/panels", "Bitte wähle einen gültigen Panel-Kanal aus.", False)
    target_channel = ctx.guild.get_channel(target_channel_id)
    if not isinstance(target_channel, discord.TextChannel) or target_channel.guild.id != ctx.guild.id:
        return back("/tickets/panels", "Der ausgewählte Panel-Kanal gehört nicht zu diesem Server.", False)

    cleaned_name = str(name).strip()[:80]
    cleaned_title = str(title).strip()[:256]
    cleaned_description = str(description).strip()[:4000]
    cleaned_footer = str(footer).strip()[:200]
    cleaned_color = str(color).strip()
    if not cleaned_name or not cleaned_title or not cleaned_description:
        return back("/tickets/panels", "Panelname, Embed-Titel und Beschreibung sind Pflichtfelder.", False)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cleaned_color):
        return back("/tickets/panels", "Die Embed-Farbe ist ungültig.", False)

    registry = pulse_ticket_category_registry()
    if len(category_keys) > 25:
        return back("/tickets/panels", "Pro Panel sind höchstens 25 Kategorien möglich.", False)
    valid_keys = []
    for key in category_keys:
        key = str(key).strip()
        if key not in registry or not registry[key].get("active", True):
            return back("/tickets/panels", "Eine ausgewählte Ticket-Kategorie ist ungültig oder deaktiviert.", False)
        if key not in valid_keys:
            valid_keys.append(key)
    if not valid_keys:
        return back("/tickets/panels", "Wähle mindestens eine Kategorie für dieses Panel aus.", False)

    settings = pulse_ticket_config()
    panels = settings.get("ticket_panels", [])
    panels = [p for p in panels if isinstance(p, dict)] if isinstance(panels, list) else []
    cleaned_panel_id = str(panel_id or "").strip()
    existing = next((p for p in panels if str(p.get("id") or "") == cleaned_panel_id), None) if cleaned_panel_id else None
    if cleaned_panel_id and existing is None:
        return back("/tickets/panels", "Das zu bearbeitende Panel wurde nicht gefunden. Bitte lade die Seite neu.", False)
    if existing is None:
        cleaned_panel_id = "panel_" + secrets.token_hex(6)
        existing = {"id": cleaned_panel_id, "created_at": utc_now_naive_iso()}
        panels.append(existing)

    existing.update({
        "id": cleaned_panel_id,
        "name": cleaned_name,
        "channel_id": target_channel_id,
        "title": cleaned_title,
        "description": cleaned_description,
        "color": cleaned_color.upper(),
        "footer": cleaned_footer or "Pulse Ticket-System",
        "category_keys": valid_keys,
    })
    if builder_v2:
        # Fields of the visual builder; older forms without them keep the stored values.
        from pulse_ticket_design import normalize_panel
        candidate = normalize_panel({
            **existing,
            "thumbnail_url": thumbnail_url, "banner_url": banner_url,
            "use_server_icon": use_server_icon, "show_load": show_load,
            "component_style": component_style, "placeholder": placeholder,
            "welcome_title": str(welcome_title).strip()[:256],
            "welcome_message": str(welcome_message).strip()[:3500],
            "ping_roles": ping_roles,
            "welcome_thumbnail_url": welcome_thumbnail_url, "welcome_banner_url": welcome_banner_url,
            "welcome_use_avatar": welcome_use_avatar,
        })
        for key, raw_url in (("thumbnail_url", thumbnail_url), ("banner_url", banner_url),
                             ("welcome_thumbnail_url", welcome_thumbnail_url), ("welcome_banner_url", welcome_banner_url)):
            if raw_url.strip() and not candidate[key]:
                return back(f"/tickets/panels?panel={cleaned_panel_id}", "Bild-URLs müssen mit https:// beginnen.", False)
        for key in ("thumbnail_url", "banner_url", "use_server_icon", "show_load", "component_style",
                    "placeholder", "welcome_title", "welcome_message", "ping_roles",
                    "welcome_thumbnail_url", "welcome_banner_url", "welcome_use_avatar"):
            existing[key] = candidate[key]
        existing["list_categories"] = bool(list_categories)
        for kind, upload, remove in (
            ("thumbnail", thumbnail_upload, remove_thumbnail),
            ("banner", banner_upload, remove_banner),
            ("welcome_thumbnail", welcome_thumbnail_upload, remove_welcome_thumbnail),
            ("welcome_banner", welcome_banner_upload, remove_welcome_banner),
        ):
            file_key = f"{kind}_file"
            if remove and existing.get(file_key):
                _remove_ticket_panel_image(existing.get(file_key), cleaned_panel_id)
                existing[file_key] = ""
            if upload is not None and upload.filename:
                stored, error = await _store_ticket_panel_image(upload, cleaned_panel_id, kind)
                if error:
                    return back(f"/tickets/panels?panel={cleaned_panel_id}", error, False)
                if existing.get(file_key):
                    _remove_ticket_panel_image(existing.get(file_key), cleaned_panel_id)
                existing[file_key] = stored
    settings["ticket_panels"] = panels
    pulse_db.set_setting("ticket_system_config", settings)
    target = f"/tickets/panels?panel={cleaned_panel_id}" if builder_v2 else "/tickets/panels"
    return back(target, f"Panel „{cleaned_name}“ wurde gespeichert. Klicke auf „Panel veröffentlichen / aktualisieren“, um es in Discord anzuzeigen.", True)


TICKET_PANEL_IMAGE_DIR = DATA_DIR / "uploads" / "ticket_panels"


async def _store_ticket_panel_image(upload: UploadFile, panel_id: str, kind: str):
    """Validate (signature + size) and store an uploaded panel image; returns (filename, error)."""
    from pulse_ticket_design import MAX_PANEL_IMAGE_BYTES, detect_image_type
    from pulse_uploads import save_image_upload
    safe_pid = re.sub(r"[^A-Za-z0-9_-]", "", panel_id)[:40] or "panel"
    # Streamed to disk in chunks: a full 8 MB read per image pushed the container RAM up.
    return await save_image_upload(
        upload, TICKET_PANEL_IMAGE_DIR, lambda ext: f"{safe_pid}_{kind}_{secrets.token_hex(4)}.{ext}",
        MAX_PANEL_IMAGE_BYTES, detect=detect_image_type,
        too_large=f"Das Bild ist zu groß (maximal {MAX_PANEL_IMAGE_BYTES // (1024 * 1024)} MB).",
        wrong_type="Nur PNG-, JPG-, GIF- oder WEBP-Bilder sind erlaubt.",
    )


def _remove_ticket_panel_image(filename: str, panel_id: str):
    """Delete only images this panel owns inside the panel image folder."""
    safe_pid = re.sub(r"[^A-Za-z0-9_-]", "", str(panel_id))[:40] or "panel"
    name = os.path.basename(str(filename or ""))
    if not name.startswith(safe_pid + "_"):
        return
    path = (TICKET_PANEL_IMAGE_DIR / name).resolve()
    if path.parent == TICKET_PANEL_IMAGE_DIR.resolve() and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


@app.get("/tickets/panels/media/{filename}")
async def pulse_ticket_panel_media(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, admin=True)
    name = os.path.basename(filename)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(png|jpg|gif|webp)", name):
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    path = contained_file(TICKET_PANEL_IMAGE_DIR, name)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    return FileResponse(path=path, headers={"Cache-Control": "private, max-age=300"})


@app.post("/tickets/panels/duplicate")
async def pulse_ticket_panel_duplicate(request: Request, panel_id: str = Form(...), user_session: str = Cookie(None)):
    auth(request, user_session, admin=True)
    settings = pulse_ticket_config()
    panels = [p for p in settings.get("ticket_panels", []) if isinstance(p, dict)] if isinstance(settings.get("ticket_panels"), list) else []
    source = next((p for p in panels if str(p.get("id") or "") == str(panel_id)), None)
    if source is None:
        return back("/tickets/panels", "Panel wurde nicht gefunden.", False)
    duplicate = {k: v for k, v in source.items() if k not in {"message_id", "published_channel_id", "thumbnail_file", "banner_file", "welcome_thumbnail_file", "welcome_banner_file"}}
    duplicate["id"] = "panel_" + secrets.token_hex(6)
    duplicate["name"] = (str(source.get("name") or "Panel") + " (Kopie)")[:80]
    duplicate["created_at"] = utc_now_naive_iso()
    panels.append(duplicate)
    settings["ticket_panels"] = panels
    pulse_db.set_setting("ticket_system_config", settings)
    return back(f"/tickets/panels?panel={duplicate['id']}", "Panel dupliziert. Hochgeladene Bilder bitte neu wählen.", True)


@app.post("/tickets/automation")
async def pulse_ticket_automation_save(
    request: Request,
    unclaimed_ping_minutes: int = Form(0),
    staff_reply_hours: int = Form(0),
    user_reminder_hours: int = Form(0),
    auto_close_hours: int = Form(0),
    escalation_minutes: int = Form(0),
    reopen_window_days: int = Form(0),
    auto_assign_enabled: bool = Form(False),
    max_open_per_staff: int = Form(5),
    user_session: str = Cookie(None),
):
    auth(request, user_session, admin=True)
    from pulse_ticket_design import automation_settings, reopen_settings
    from pulse_ticket_distribution import distribution_settings
    settings = pulse_ticket_config()
    current_automation = automation_settings(settings.get("automation"))
    category_sla = {
        key: dict(value)
        for key, value in current_automation.get("category_sla", {}).items()
        if isinstance(value, dict)
    }
    form = await request.form()
    pattern = re.compile(r"^sla__([a-z0-9_-]{1,80})__(unclaimed_ping_minutes|staff_reply_hours|user_reminder_hours|escalation_minutes)$")
    for field, raw_value in form.multi_items():
        match = pattern.fullmatch(str(field))
        if not match:
            continue
        category, key = match.groups()
        raw_value = str(raw_value or "").strip()
        if not raw_value:
            category_sla.get(category, {}).pop(key, None)
        else:
            try:
                category_sla.setdefault(category, {})[key] = int(raw_value)
            except (TypeError, ValueError):
                return back("/tickets/panels?tab=automation", f"Ungültiger SLA-Wert für Kategorie {category}.", False)
        if category in category_sla and not category_sla[category]:
            category_sla.pop(category, None)

    settings["automation"] = automation_settings({
        "unclaimed_ping_minutes": unclaimed_ping_minutes,
        "staff_reply_hours": staff_reply_hours,
        "user_reminder_hours": user_reminder_hours,
        "auto_close_hours": auto_close_hours,
        "escalation_minutes": escalation_minutes,
        "category_sla": category_sla,
    })
    settings["reopen"] = reopen_settings({"window_days": reopen_window_days})
    settings["distribution"] = distribution_settings({
        "enabled": auto_assign_enabled,
        "max_open_per_staff": max_open_per_staff,
    })
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels?tab=automation", "Ticket-Automatik gespeichert.", True)


@app.post("/tickets/panels/publish")
async def pulse_ticket_panel_publish(
    request: Request,
    panel_id: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets/panels", "Pulse-Bot ist nicht bereit. Das Panel wurde nicht veröffentlicht.", False)
    cog = bot.get_cog("Tickets")
    if not cog or not hasattr(cog, "publish_panel_for_guild"):
        return back("/tickets/panels", "Der neue Pulse-Panel-Builder ist im laufenden Bot noch nicht geladen. Bitte den Host-Prozess neu starten.", False)
    try:
        result = await cog.publish_panel_for_guild(ctx.guild, panel_id)
    except ValueError as exc:
        return back("/tickets/panels", str(exc), False)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Panel {panel_id} konnte nicht veröffentlicht werden ({type(exc).__name__}).")
        return back("/tickets/panels", "Discord hat das Veröffentlichen abgelehnt. Prüfe Kanalzugriff sowie Rechte zum Senden von Nachrichten und Einbettungen.", False)
    return back("/tickets/panels", f"Panel „{result.get('panel_name', 'Panel')}“ wurde in {result.get('channel', 'Discord')} veröffentlicht bzw. aktualisiert.", True)


@app.post("/tickets/panels/delete")
async def pulse_ticket_panel_delete(
    request: Request,
    panel_id: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    settings = pulse_ticket_config()
    panels = settings.get("ticket_panels", [])
    panels = [p for p in panels if isinstance(p, dict)] if isinstance(panels, list) else []
    panel = next((p for p in panels if str(p.get("id") or "") == str(panel_id)), None)
    if panel is None:
        return back("/tickets/panels", "Panel wurde nicht gefunden.", False)
    bot = getattr(request.app.state, "bot", None)
    message_id = panel.get("message_id")
    destination_id = panel.get("published_channel_id") or panel.get("channel_id")
    if message_id and bot and bot.is_ready():
        try:
            destination = ctx.guild.get_channel(int(destination_id or 0))
            if isinstance(destination, discord.TextChannel):
                message = await destination.fetch_message(int(message_id))
                await message.delete(reason="Pulse-Panel über die Website gelöscht")
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
            print(f"⚠️ Pulse-Panel {panel_id} konnte nicht entfernt werden ({type(exc).__name__}).")
            return back("/tickets/panels", "Die Panel-Nachricht konnte nicht entfernt werden. Prüfe die Rechte des Bots; die Konfiguration bleibt erhalten.", False)
    settings["ticket_panels"] = [p for p in panels if str(p.get("id") or "") != str(panel_id)]
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels", "Panel und seine Konfiguration wurden gelöscht.", True)


@app.post("/tickets/categories/save")
async def pulse_ticket_category_save(
    request: Request,
    name: str = Form(...),
    prefix: str = Form(...),
    emoji: str = Form("🎫"),
    description: str = Form(...),
    max_open: int = Form(0),
    parent_category_id: str = Form(""),
    claimed_category_id: str = Form(""),
    opening_message: str = Form(""),
    group: str = Form("team"),
    category_id: str = Form(""),
    role_ids: list[str] = Form(default=[]),
    ping_role_ids: list[str] = Form(default=[]),
    channel_label: str = Form(""),
    ping_roles_field: str = Form(""),
    welcome_title: str = Form(""),
    button_style: str = Form("primary"),
    builder_v2: bool = Form(False),
    return_to: str = Form(""),
    welcome_banner_url: str = Form(""),
    remove_welcome_banner: str = Form(""),
    welcome_banner_upload: UploadFile | None = File(None),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    cleaned_banner_url = str(welcome_banner_url or "").strip()[:500]
    if cleaned_banner_url and not re.fullmatch(r"https://[^\s<>\"']{4,500}", cleaned_banner_url):
        return back("/tickets/panels?tab=categories", "Die Banner-URL muss mit https:// beginnen.", False)
    if builder_v2:
        # Questions arrive as q_label_1..5 (+placeholder/required/long) from the visual builder.
        form = await request.form()
        from pulse_ticket_design import BUTTON_STYLES, clean_questions
        questions = clean_questions([
            {
                "label": form.get(f"q_label_{i}", ""),
                "placeholder": form.get(f"q_placeholder_{i}", ""),
                "required": bool(form.get(f"q_required_{i}")),
                "long": bool(form.get(f"q_long_{i}")),
            }
            for i in range(1, 6)
        ])
        if button_style not in BUTTON_STYLES:
            button_style = "primary"
    cleaned_name = str(name).strip()[:80]
    cleaned_prefix = str(prefix).strip().lower()
    cleaned_emoji = str(emoji).strip()[:100] or "🎫"
    cleaned_description = str(description).strip()[:100]
    cleaned_opening = str(opening_message).strip()[:1800]
    if not cleaned_name or not cleaned_description:
        return back("/tickets/panels", "Kategorien benötigen einen Namen und eine Beschreibung.", False)
    if not re.fullmatch(r"[a-z0-9_-]{1,24}", cleaned_prefix):
        return back("/tickets/panels", "Das Präfix darf nur Buchstaben, Zahlen, Bindestriche und Unterstriche enthalten (max. 24 Zeichen).", False)
    if group not in {"team", "highteam", "leadership"}:
        return back("/tickets/panels", "Ungültiger Bearbeitungsbereich.", False)
    if not 0 <= int(max_open) <= 1000:
        return back("/tickets/panels", "Die Auslastungsgrenze muss zwischen 0 und 1000 liegen.", False)

    parent_id = 0
    if str(parent_category_id or "").strip():
        try:
            parent_id = int(parent_category_id)
        except (TypeError, ValueError):
            return back("/tickets/panels", "Ungültige Discord-Kategorie.", False)
        parent = ctx.guild.get_channel(parent_id)
        if not isinstance(parent, discord.CategoryChannel) or parent.guild.id != ctx.guild.id:
            return back("/tickets/panels", "Die ausgewählte Ticket-Zielkategorie gehört nicht zu diesem Server.", False)

    claimed_id = 0
    if str(claimed_category_id or "").strip():
        try:
            claimed_id = int(claimed_category_id)
        except (TypeError, ValueError):
            return back("/tickets/panels", "Ungültige Discord-Kategorie für übernommene Tickets.", False)
        claimed_parent = ctx.guild.get_channel(claimed_id)
        if not isinstance(claimed_parent, discord.CategoryChannel) or claimed_parent.guild.id != ctx.guild.id:
            return back("/tickets/panels", "Die Kategorie für übernommene Tickets gehört nicht zu diesem Server.", False)

    role_values = []
    for raw in role_ids:
        try:
            role_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets/panels", "Ungültige Rollen-ID.", False)
        role = ctx.guild.get_role(role_id)
        if role is None or role.is_default() or role.managed:
            return back("/tickets/panels", "Bitte wähle nur gültige, normale Serverrollen.", False)
        if role_id not in role_values:
            role_values.append(role_id)

    ping_values = []
    for raw in ping_role_ids:
        try:
            ping_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets/panels?tab=categories", "Ungültige Ping-Rolle.", False)
        ping_role = ctx.guild.get_role(ping_id)
        if ping_role is None or ping_role.is_default() or ping_role.managed:
            return back("/tickets/panels?tab=categories", "Bitte wähle als Ping nur normale Serverrollen (kein @everyone, keine Bot-Rollen).", False)
        if ping_id not in ping_values:
            ping_values.append(ping_id)
    if len(ping_values) > 10:
        return back("/tickets/panels?tab=categories", "Höchstens 10 Ping-Rollen pro Kategorie.", False)

    registry = pulse_ticket_category_registry()
    key = str(category_id or "").strip()
    if key:
        if key not in registry:
            return back("/tickets/panels", "Die zu bearbeitende Kategorie wurde nicht gefunden.", False)
    else:
        key = "custom_" + secrets.token_hex(5)
        while key in registry:
            key = "custom_" + secrets.token_hex(5)
    settings = pulse_ticket_config()
    definitions = settings.get("ticket_categories", [])
    definitions = [item for item in definitions if isinstance(item, dict)] if isinstance(definitions, list) else []
    category_value = {
        "id": key,
        "name": cleaned_name,
        "prefix": cleaned_prefix,
        "emoji": cleaned_emoji,
        "description": cleaned_description,
        "max_open": int(max_open),
        "group": group,
        "role_ids": role_values,
        "parent_category_id": parent_id,
        "claimed_category_id": claimed_id,
        "opening_message": cleaned_opening,
        "active": True,
    }
    previous = next((item for item in definitions if str(item.get("id") or "") == key), {})
    if ping_roles_field:
        # The builder sends this marker, so an empty selection really means "nobody is pinged".
        category_value["ping_role_ids"] = ping_values
    elif "ping_role_ids" in previous:
        category_value["ping_role_ids"] = previous["ping_role_ids"]
    if builder_v2:
        category_value.update({
            "welcome_title": str(welcome_title).strip()[:256],
            "questions": questions,
            "button_style": button_style,
            "welcome_banner_url": cleaned_banner_url,
            "welcome_banner_file": str(previous.get("welcome_banner_file") or ""),
            "channel_label": re.sub(r"\s+", " ", str(channel_label or "")).strip()[:40],
        })
        # Own welcome banner per category (uploaded file > URL > panel banner).
        owner = f"cat-{key}"
        if remove_welcome_banner and category_value["welcome_banner_file"]:
            _remove_ticket_panel_image(category_value["welcome_banner_file"], owner)
            category_value["welcome_banner_file"] = ""
        if welcome_banner_upload is not None and welcome_banner_upload.filename:
            stored, error = await _store_ticket_panel_image(welcome_banner_upload, owner, "welcome_banner")
            if error:
                return back("/tickets/panels?tab=categories", error, False)
            if category_value["welcome_banner_file"]:
                _remove_ticket_panel_image(category_value["welcome_banner_file"], owner)
            category_value["welcome_banner_file"] = stored
    else:
        # Older forms don't send these fields; keep what the visual builder stored.
        for extra in ("welcome_title", "questions", "button_style", "claimed_category_id",
                      "welcome_banner_url", "welcome_banner_file", "channel_label"):
            if extra in previous:
                category_value[extra] = previous[extra]
    replaced = False
    for index, item in enumerate(definitions):
        if str(item.get("id") or "") == key:
            definitions[index] = category_value
            replaced = True
            break
    if not replaced:
        definitions.append(category_value)
    settings["ticket_categories"] = definitions
    pulse_db.set_setting("ticket_system_config", settings)
    target = return_to if re.fullmatch(r"/tickets/panels(\?[A-Za-z0-9_=&-]{0,120})?", str(return_to or "")) else "/tickets/panels"
    return back(target, f"Kategorie „{cleaned_name}“ wurde gespeichert und steht im Panel-Builder zur Auswahl.", True)


@app.post("/tickets/categories/delete")
async def pulse_ticket_category_delete(
    request: Request,
    category_id: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    if category_id in {"support", "bug", "unban", "highteam", "leadership"}:
        return back("/tickets/panels", "Standardkategorien können nicht gelöscht, aber zurückgesetzt werden.", False)
    settings = pulse_ticket_config()
    definitions = settings.get("ticket_categories", [])
    definitions = [item for item in definitions if isinstance(item, dict)] if isinstance(definitions, list) else []
    if not any(str(item.get("id") or "") == str(category_id) for item in definitions):
        return back("/tickets/panels", "Eigene Kategorie wurde nicht gefunden.", False)
    found = False
    for item in definitions:
        if str(item.get("id") or "") == str(category_id):
            # Keep metadata so already-open or historic tickets retain their privacy and display labels.
            item["active"] = False
            if item.get("welcome_banner_file"):
                _remove_ticket_panel_image(item["welcome_banner_file"], f"cat-{category_id}")
                item["welcome_banner_file"] = ""
            found = True
            break
    if not found:
        return back("/tickets/panels", "Eigene Kategorie wurde nicht gefunden.", False)
    settings["ticket_categories"] = definitions
    panels = settings.get("ticket_panels", [])
    if isinstance(panels, list):
        for panel in panels:
            if isinstance(panel, dict) and isinstance(panel.get("category_keys"), list):
                panel["category_keys"] = [key for key in panel["category_keys"] if str(key) != str(category_id)]
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels", "Eigene Kategorie wurde deaktiviert und aus allen Panel-Auswahlen entfernt. Bestehende Tickets behalten ihre Rollen- und Kategorieinfos.", True)


@app.get("/tickets/transcript/{ticket_id}")
async def pulse_ticket_transcript(request: Request, ticket_id: str, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_manage_tickets")
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not pulse_ticket_access.can_access_ticket(ctx, ticket):
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden.")
    transcript_name = os.path.basename(str(ticket.get("transcript_path") or ""))
    if not transcript_name or transcript_name != f"{ticket_id}.txt":
        raise HTTPException(status_code=404, detail="Für dieses Ticket ist kein Transkript gespeichert.")
    transcript_dir = (DATA_DIR / "transcripts").resolve()
    transcript_file = (transcript_dir / transcript_name).resolve()
    if transcript_file.parent != transcript_dir or not transcript_file.is_file():
        raise HTTPException(status_code=404, detail="Transkriptdatei ist nicht vorhanden.")
    return FileResponse(path=str(transcript_file), media_type="text/plain; charset=utf-8", filename=transcript_name)


def _ticket_transcript_file(ticket):
    transcript_name = os.path.basename(str(ticket.get("transcript_path") or ""))
    if not transcript_name or transcript_name != f"{ticket.get('id')}.txt":
        return None
    transcript_dir = (DATA_DIR / "transcripts").resolve()
    transcript_file = (transcript_dir / transcript_name).resolve()
    return transcript_file if transcript_file.parent == transcript_dir and transcript_file.is_file() else None


@app.get("/tickets/transcript/{ticket_id}/view", response_class=HTMLResponse)
async def pulse_ticket_transcript_view(request: Request, ticket_id: str, user_session: str = Cookie(None)):
    """BOT-06: the stored transcript as a readable page (the download stays at /tickets/transcript/{id})."""
    import pulse_ticket_extras
    from pulse_pro import render_pro_page
    ctx = auth(request, user_session, perm="can_manage_tickets")
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not pulse_ticket_access.can_access_ticket(ctx, ticket):
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden.")
    path = _ticket_transcript_file(ticket)
    if path is None:
        raise HTTPException(status_code=404, detail="Für dieses Ticket ist kein Transkript gespeichert.")
    text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">📄 Transkript {esc(ticket_id)}</div>'
            f'<div class="pulse-section-sub">Referenz <code>TK:{esc(ticket_id)}</code> · {esc(ticket.get("user_name"))} · {esc(ticket.get("category"))} · geschlossen {esc(str(ticket.get("closed_at") or "")[:16])}</div></div>'
            f'<div class="flex gap-2"><a class="pulse-btn ghost" href="/tickets?status=closed">← Tickets</a>'
            f'<a class="pulse-btn ghost" href="/tickets/transcript/{quote(ticket_id)}">⬇ Download</a></div></div>'
            '<style>.tr-msg{padding:8px 10px;border-radius:10px;background:var(--surface2);margin-bottom:6px}.tr-meta{font-size:11px;color:var(--muted)}'
            '.tr-meta b{color:inherit}.tr-text{font-size:13px;white-space:pre-wrap;word-break:break-word}.tr-line{font-size:11px;color:var(--muted);margin:4px 0}</style>'
            f'<section class="pulse-card"><div class="pulse-card-b">{pulse_ticket_extras.transcript_html(text)}</div></section>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules["webserver"], f"Transkript {ticket_id}", ctx, "tickets", body))


def _checklist_ctx(request, user_session):
    ctx = auth(request, user_session, perm=None)
    if not (ctx.perms.get("is_admin") or ctx.perms.get("can_promote")):
        raise HTTPException(status_code=403, detail="On- und Offboarding verwaltet die Leitung.")
    return ctx


@app.get("/team/checklists", response_class=HTMLResponse)
async def team_checklists_page(request: Request, user_session: str = Cookie(None)):
    """BOT-11: open on-/offboarding checklists; leadership ticks steps and assigns mentors."""
    import sys

    import pulse_checklists as pc
    from pulse_pro import render_pro_page
    ctx = _checklist_ctx(request, user_session)
    team_ids = set(int(x) for x in ctx.config.get("team_role_ids", []) if str(x).isdigit())
    team = sorted((m for m in getattr(ctx.guild, "members", []) if not m.bot and any(r.id in team_ids for r in m.roles)),
                  key=lambda m: m.display_name.lower())

    def card(item):
        steps = "".join(
            f'<form method="post" action="/team/checklists/toggle" class="flex items-center gap-2">'
            f'<input type="hidden" name="checklist_id" value="{esc(item["id"])}"><input type="hidden" name="step" value="{esc(s["key"])}">'
            f'<button class="pulse-btn ghost" type="submit" aria-pressed="{"true" if s["done"] else "false"}">{"✅" if s["done"] else "⬜"}</button>'
            f'<span class="text-xs">{esc(s["label"])}{" · " + esc(s["done"]["by"]) if s["done"] else ""}</span></form>'
            for s in item["steps"])
        mentor = ""
        if item["kind"] == "onboarding":
            options = "".join(f'<option value="{m.id}"{" selected" if str(m.id) == str(item.get("mentor_id")) else ""}>{esc(m.display_name)}</option>'
                              for m in team if str(m.id) != str(item["user_id"]))
            mentor = (f'<form method="post" action="/team/checklists/mentor" class="flex flex-wrap gap-2 items-center mt-2">'
                      f'<input type="hidden" name="checklist_id" value="{esc(item["id"])}">'
                      f'<select name="mentor_id" class="pulse-input" style="width:auto" aria-label="Mentor wählen"><option value="">Mentor wählen…</option>{options}</select>'
                      f'<button class="pulse-btn ghost" type="submit">Speichern</button></form>')
        title = "🟢 Onboarding" if item["kind"] == "onboarding" else "🚪 Offboarding"
        return (f'<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">{title} · {esc(item["user_name"])}</div>'
                f'<div class="pulse-section-sub">seit {time_tag(item["created_at"])} · {item["progress"]}/{len(item["steps"])} erledigt'
                f'{" · Mentor: " + esc(item["mentor_name"]) if item.get("mentor_name") else ""}</div></div>'
                f'<a class="pulse-btn ghost" href="/member/{esc(item["user_id"])}">Teamakte</a></div>'
                f'<div class="pulse-card-b space-y-1">{steps}{mentor}</div></section>')
    open_items = pc.list_open()
    done_items = pc.list_done()
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">☑ On- &amp; Offboarding</div>'
            f'<div class="pulse-section-sub">Checklisten entstehen automatisch beim Teambeitritt und -austritt. Team-Offboarding entfernt nur Teamrollen – kein Server-Kick.</div></div></div>'
            f'<div class="space-y-4">{"".join(card(i) for i in open_items) or empty_state("Keine offenen Checklisten.", "☑")}</div>'
            + (f'<section class="pulse-card mt-4"><div class="pulse-card-h"><div class="pulse-section-title">Abgeschlossen</div></div><div class="pulse-card-b space-y-1">'
               + "".join(f'<div class="pulse-row"><div class="pulse-row-title">{"🟢" if i["kind"] == "onboarding" else "🚪"} {esc(i["user_name"])}</div>'
                         f'<div class="pulse-row-meta">{time_tag(i["completed_at"])}</div></div>' for i in done_items) + '</div></section>'
               if done_items else ""))
    return HTMLResponse(render_pro_page(sys.modules["webserver"], "On- & Offboarding", ctx, "team-checklists", body))


@app.post("/team/checklists/toggle")
async def team_checklists_toggle(request: Request, user_session: str = Cookie(None)):
    import pulse_checklists as pc
    ctx = _checklist_ctx(request, user_session)
    form = await request.form()
    actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
    try:
        item = pc.toggle(str(form.get("checklist_id") or ""), str(form.get("step") or ""), actor)
    except ValueError as exc:
        return back("/team/checklists", str(exc), False)
    if item.get("completed_at"):
        log_audit(actor, ctx.user["id"], "Checkliste abgeschlossen", f"{item['kind']} · {item['user_name']} ({item['user_id']})")
        return back("/team/checklists", f"Checkliste für {item['user_name']} abgeschlossen.")
    return back("/team/checklists")


@app.post("/team/checklists/mentor")
async def team_checklists_mentor(request: Request, user_session: str = Cookie(None)):
    import pulse_checklists as pc
    ctx = _checklist_ctx(request, user_session)
    form = await request.form()
    mentor_id = str(form.get("mentor_id") or "")
    mentor = ctx.guild.get_member(int(mentor_id)) if mentor_id.isdigit() else None
    checklist = pc.get(str(form.get("checklist_id") or ""))
    team_ids = {int(x) for x in ctx.config.get("team_role_ids", []) if str(x).isdigit()}
    # review fix: only a team member, and not the person being onboarded
    if mentor is None or mentor.bot or not any(r.id in team_ids for r in mentor.roles) \
            or (checklist and str(checklist["user_id"]) == str(mentor.id)):
        return back("/team/checklists", "Bitte ein anderes Teammitglied als Mentor wählen.", False)
    actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
    try:
        item = pc.set_mentor(str(form.get("checklist_id") or ""), mentor.id, mentor.display_name, actor)
    except ValueError as exc:
        return back("/team/checklists", str(exc), False)
    log_audit(actor, ctx.user["id"], "Mentor zugewiesen", f"{mentor.display_name} für {item['user_name']}")
    return back("/team/checklists", f"{mentor.display_name} begleitet jetzt {item['user_name']}.")


def _mod_case_ctx(request, user_session):
    ctx = auth(request, user_session, perm=None)
    if not (ctx.perms.get("is_admin") or ctx.perms.get("can_moderate_kicks") or ctx.perms.get("can_moderate_bans")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir ein Moderationsrecht.")
    return ctx


@app.get("/mod-cases", response_class=HTMLResponse)
async def mod_cases_page(request: Request, user_session: str = Cookie(None)):
    """BOT-09: case file of the citizen moderation (/mod …) – search, pages, CSV."""
    import pulse_lists
    import pulse_mod_cases as mc
    from pulse_pro import render_pro_page
    ctx = _mod_case_ctx(request, user_session)
    params = pulse_lists.params(request.query_params)
    rows = pulse_lists.search(mc.list_cases(), params["q"],
                              lambda c: f"{c['id']} {c['target_name']} {c['target_id']} {c['reason']} {c['actor_name']} {c['action']}")
    page_rows, meta = pulse_lists.paginate(rows, params["page"], params["per"])
    state = {"active": ("aktiv", "warn"), "expired": ("abgelaufen", "good"), "failed": ("fehlgeschlagen", "bad")}
    items = "".join(
        f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><code>{esc(c["id"])}</code> · '
        f'{esc(mc.ACTIONS.get(c["action"], c["action"]))} · {esc(c["target_name"])} <span class="pulse-row-meta">({esc(c["target_id"])})</span> '
        f'<span class="pulse-pill {state.get(c["status"], ("", ""))[1]}">{esc(state.get(c["status"], (c["status"], ""))[0])}</span></div>'
        f'<div class="pulse-row-meta">{time_tag(c["created_at"])} · von {esc(c["actor_name"])}'
        f'{" · bis " + esc(str(c["expires_at"])[:16].replace("T", " ")) + " UTC" if c.get("expires_at") else ""}'
        f'{" · DM zugestellt" if c.get("dm_sent") else " · keine DM"}</div>'
        f'<div class="text-xs mt-1">{esc(c["reason"])}</div>'
        + (f'<div class="text-xs mt-1">Beweis: <a class="text-indigo-500" rel="noopener noreferrer" target="_blank" href="{esc(safe_url(c["proof"]))}">{esc(c["proof"][:80])}</a></div>'
           if c.get("proof") and safe_url(c["proof"]) else "")
        + '</div></div>' for c in page_rows) or empty_state("Noch keine Moderationsfälle.", "⚖")
    appeal_rows = mc.list_appeals(getattr(getattr(ctx, "guild", None), "id", None), status="pending", limit=25)
    appeal_items = "".join(
        f'<div class="pulse-row"><div class="pulse-row-main">'
        f'<div class="pulse-row-title"><code>{esc(row["case_id"])}</code> · '
        f'<span class="pulse-pill warn">offen</span></div>'
        f'<div class="pulse-row-meta">Betroffene Person: {esc(row["target_id"])} · '
        f'{esc(row["submitted_at"][:16].replace("T", " "))} UTC · '
        f'Frist: {esc(row["deadline_at"][:10])}</div>'
        f'<div class="text-xs mt-1">{esc(row["reason"])}</div>'
        f'</div></div>'
        for row in appeal_rows
    ) or empty_state("Keine offenen Einsprüche.", "⚖")
    appeal_section = (
        '<section class="pulse-card mb-4"><div class="pulse-card-h"><div>'
        '<div class="pulse-section-title">⚖ Offene Einsprüche</div>'
        '<div class="pulse-section-sub">Einsprüche werden von berechtigten Moderatoren geprüft. '
        'Entscheidungen lösen keine Sanktionen automatisch auf.</div></div></div>'
        f'<div class="pulse-card-b space-y-2">{appeal_items}</div></section>'
    )
    q = params["q"]
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">⚖ Bürger-Moderation</div>'
            f'<div class="pulse-section-sub">Fallakte aller /mod-Aktionen. Neue Fälle entstehen nur in Discord über /mod warn|timeout|kick|ban.</div></div>'
            f'<div class="flex gap-2"><a class="pulse-btn ghost" href="/mod-cases/verlauf">🗂 Gesamtverlauf</a>'
            f'<a class="pulse-btn ghost" href="/mod-cases.csv{("?q=" + quote(q)) if q else ""}">⬇ CSV</a></div></div>'
            + appeal_section + __import__("pulse_views").bar("/mod-cases", ctx.user["id"], request.query_params.multi_items()) +
            f'<section class="pulse-card"><div class="pulse-card-b"><form method="get" class="pulse-search mb-4" role="search" style="max-width:none">'
            f'<span>⌕</span><input name="q" value="{esc(q)}" aria-label="Fälle durchsuchen" placeholder="Fall-ID, Name, Grund, Moderator…"><button class="pulse-btn primary">Suchen</button></form>'
            f'<div class="space-y-2">{items}</div>{pulse_lists.pager("/mod-cases", meta, {"q": q})}</div></section>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules["webserver"], "Bürger-Moderation", ctx, "mod-cases", body))


@app.get("/mod-cases.csv")
async def mod_cases_csv(request: Request, user_session: str = Cookie(None)):
    import pulse_lists
    import pulse_mod_cases as mc
    ctx = _mod_case_ctx(request, user_session)
    q = str(request.query_params.get("q") or "")
    rows = pulse_lists.search(mc.list_cases(), q, lambda c: f"{c['id']} {c['target_name']} {c['target_id']} {c['reason']} {c['actor_name']} {c['action']}")
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Moderationsfälle exportiert", f"{len(rows)} Fälle")
    return pulse_lists.csv_response(f"pulse_moderation_{now_de().strftime('%Y-%m-%d')}.csv",
                                    ["Fall", "Aktion", "Person", "Discord-ID", "Grund", "Beweis", "Bis", "Moderator", "Erstellt", "Status"],
                                    [(c["id"], mc.ACTIONS.get(c["action"], c["action"]), c["target_name"], c["target_id"], c["reason"],
                                      c.get("proof"), c.get("expires_at"), c["actor_name"], c["created_at"], c["status"]) for c in rows])


@app.get("/mod-cases/verlauf", response_class=HTMLResponse)
async def mod_cases_history_page(request: Request, user_session: str = Cookie(None)):
    """Audit #11/#12: shared case references and one history across all case files (read-only)."""
    import pulse_case_registry as reg
    from pulse_pro import render_pro_page
    ctx = auth(request, user_session, perm=None)
    kinds = reg.allowed_kinds(ctx.perms)
    if not reg.allowed_kinds(ctx.perms, include_reference_only=True):  # #116: ticket/report managers may open their references
        raise HTTPException(status_code=403, detail="Dafür fehlt dir ein Moderations-, Warn- oder Leitungsrecht.")
    guild_id = getattr(getattr(ctx, "guild", None), "id", None)
    ref_q = str(request.query_params.get("ref") or "").strip()[:60]
    user_q = re.sub(r"\D", "", str(request.query_params.get("user") or ""))[:20]
    found = None
    notice = ""
    if ref_q:
        found = reg.resolve(ref_q, ctx.perms, guild_id=guild_id)
        if found:
            user_q = user_q or found.get("person_id", "")
        else:
            notice = "Keine sichtbare Akte zu dieser Referenz gefunden (unbekannt oder ohne Recht für diese Fallart)."
    history = reg.person_history(user_q, ctx.perms, guild_id=guild_id) if user_q else None
    if ref_q or user_q:
        log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Fall-Gesamtverlauf eingesehen",
                  f"Referenz {ref_q or '–'} · Person {user_q or '–'}")

    def row(e: dict) -> str:
        """A history event (``event`` set) or a single case from a reference lookup."""
        link = (f' · <a class="text-indigo-500" href="{esc(e["url"])}">Akte öffnen</a>' if e.get("url") else "")
        appeal = f' · Einspruch: {esc(e["appeal_status"])}' if e.get("appeal_status") else ""
        when = e.get("at") if "event" in e else e.get("created_at")
        actor = e.get("event_actor") if "event" in e else e.get("actor")
        detail = e.get("event_detail") if "event" in e else e.get("detail")
        step = f'<b>{esc(e["event_label"])}</b> · ' if "event" in e else ""
        detail_html = f'<div class="text-xs mt-1">{esc(detail)}</div>' if detail else ""
        return (f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{step}<code>{esc(e["ref"])}</code> · '
                f'{esc(e["kind_label"])} · {esc(e["title"])} <span class="pulse-pill">{esc(e["status_label"])}</span></div>'
                f'<div class="pulse-row-meta">{time_tag(when) if when else "Zeit unbekannt"}'
                f'{" · von " + esc(actor) if actor else ""}{appeal}{link}</div>'
                f'{detail_html}</div></div>')

    labels = ", ".join(reg.KINDS[k] for k in reg.KIND_ORDER if k in kinds)
    parts = []
    if notice:
        parts.append(f'<div class="pulse-card mb-4"><div class="pulse-card-b text-sm">{esc(notice)}</div></div>')
    if found:
        parts.append(f'<section class="pulse-card mb-4"><div class="pulse-card-h"><div class="pulse-section-title">🔎 Gefundene Akte</div></div>'
                     f'<div class="pulse-card-b">{row(found)}</div></section>')
    if history is not None:
        import pulse_case_repeat
        hint = pulse_case_repeat.repeat_hint(user_q, ctx.perms, guild_id=guild_id)
        limits = (" · " + esc("; ".join(hint["limits"]))) if hint["limits"] else ""
        parts.append(f'<section class="pulse-card mb-4"><div class="pulse-card-b"><b>{esc(hint["text"])}</b>'
                     + (f'<div class="text-xs mt-1">{esc(", ".join(hint["refs"]))}</div>' if hint["refs"] else "")
                     + f'<div class="text-xs mt-1">{esc(hint["rule"])}{limits}</div></div></section>')
        missing = "".join(f'<div class="text-xs text-amber-600">⚠ {esc(reg.KINDS[k])} konnte gerade nicht gelesen werden.</div>'
                          for k in history["unavailable"])
        items = "".join(row(e) for e in history["entries"]) or empty_state("Keine Fälle zu dieser Person in den für dich sichtbaren Akten.", "⚖")
        parts.append(f'<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">🗂 Gesamtverlauf · {esc(user_q)}</div>'
                     f'<div class="pulse-section-sub">Neueste zuerst · {len(history["entries"])} von {history["total"]} Ereignissen · '
                     f'verknüpft nur über die gespeicherte Discord-ID</div></div></div>'
                     f'<div class="pulse-card-b space-y-2">{missing}{items}</div></section>')
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">🗂 Fall-Gesamtverlauf</div>'
            f'<div class="pulse-section-sub">Eine Referenz und ein Verlauf für alle Fallakten. Sichtbar für dich: {esc(labels)}. '
            f'Nur lesend; jede Akte bleibt in ihrem eigenen Bereich.</div></div>'
            f'<a class="pulse-btn ghost" href="/mod-cases">⚖ Bürger-Moderation</a></div>'
            f'<section class="pulse-card mb-4"><div class="pulse-card-b"><form method="get" class="flex flex-wrap gap-2" role="search">'
            f'<input class="pulse-input" name="ref" value="{esc(ref_q)}" aria-label="Fallreferenz" placeholder="Referenz, z. B. BM:MOD-1A2B3C oder PX-2026-0001">'
            f'<input class="pulse-input" name="user" value="{esc(user_q)}" inputmode="numeric" aria-label="Discord-ID" placeholder="Discord-ID der Person">'
            f'<button class="pulse-btn primary">Anzeigen</button></form>'
            f'<div class="text-xs mt-2">Fallarten: BM = Bürger-Moderation, TF = Team-/Spielerfall, TW = Teamwarnung, KI = KI-Moderation.</div>'
            f'</div></section>' + "".join(parts))
    import sys
    return HTMLResponse(render_pro_page(sys.modules["webserver"], "Fall-Gesamtverlauf", ctx, "mod-cases", body))


def _mod_stats_bars(items, total: int) -> str:
    """Labelled horizontal bars; widths come only from the given counts."""
    rows = []
    for label, count, extra in items:
        width = round(count * 100 / total) if total else 0
        rows.append(f'<div class="mb-3"><div class="flex justify-between text-xs mb-1"><span>{esc(label)}</span>'
                    f'<span class="font-semibold">{count}{esc(extra)}</span></div>'
                    f'<div class="pulse-progress" role="img" aria-label="{esc(label)}: {count}"><span style="width:{width}%"></span></div></div>')
    return "".join(rows)


def _mod_stats_value(value, suffix: str = "") -> str:
    return "–" if value is None else f"{value}{suffix}"


@app.get("/mod-cases/beweise", response_class=HTMLResponse)
async def mod_case_evidence_page(request: Request, user_session: str = Cookie(None)):
    """Audit #14: one evidence list per case reference (managed records plus legacy evidence, read-only)."""
    import pulse_case_evidence as ev
    from pulse_pro import render_pro_page
    ctx = auth(request, user_session, perm=None)
    ref = str(request.query_params.get("ref") or "").strip()[:60]
    notice = str(request.query_params.get("msg") or "")[:200]
    guild_id = getattr(getattr(ctx, "guild", None), "id", None)
    data = ev.evidence_for(ref, ctx.perms, guild_id=guild_id) if ref else None
    body_parts = []
    if notice:
        body_parts.append(f'<div class="pulse-card mb-4"><div class="pulse-card-b text-sm">{esc(notice)}</div></div>')
    if ref and not data:
        body_parts.append('<div class="pulse-card mb-4"><div class="pulse-card-b text-sm">Keine sichtbare Akte zu dieser Referenz (unbekannt oder ohne Recht für diese Fallart).</div></div>')
    if data:
        def line(item):
            label = esc(item["value"][:120]) if item["kind"] != "file" else esc(item["value"])
            shown = (f'<a class="text-indigo-500" rel="noopener noreferrer" target="_blank" href="{esc(item["url"])}">{label}</a>'
                     if item.get("url") else label)
            removed = (f' · <b>entfernt</b> von {esc(item.get("removed_by_name") or "")}: {esc(item.get("remove_reason") or "")}'
                       if item.get("removed") else "")
            form = ""
            if item["source"] == "managed" and not item.get("removed"):
                form = (f'<form method="post" action="/mod-cases/beweise/remove" class="flex gap-2 mt-1">'
                        f'<input type="hidden" name="ref" value="{esc(data["ref"])}"><input type="hidden" name="evidence_id" value="{esc(item["id"])}">'
                        f'<input class="pulse-input" name="reason" required minlength="5" maxlength="300" aria-label="Grund der Entfernung" placeholder="Grund der Entfernung">'
                        f'<button class="pulse-btn ghost">Entfernen</button></form>')
            origin = "Altbestand" if item["source"] == "legacy" else f'hinzugefügt von {esc(item.get("added_by") or "")} · {esc(str(item.get("added_at") or "")[:16])}'
            return (f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{shown}</div>'
                    f'<div class="pulse-row-meta">{esc(ev.KINDS.get(item["kind"], "Datei"))} · {origin}{removed}'
                    f'{(" · " + esc(item["note"])) if item.get("note") else ""}</div>{form}</div></div>')
        rows = "".join(line(i) for i in data["legacy"] + data["managed"]) or empty_state("Noch keine Beweise zu diesem Fall.", "📎")
        add = (f'<form method="post" action="/mod-cases/beweise/add" class="flex flex-wrap gap-2 mt-3">'
               f'<input type="hidden" name="ref" value="{esc(data["ref"])}">'
               f'<select class="pulse-input" name="kind" aria-label="Art"><option value="link">Link</option><option value="text">Notiz</option></select>'
               f'<input class="pulse-input" name="value" required maxlength="{ev.MAX_LINK}" aria-label="Inhalt" placeholder="https://… oder Notiz">'
               f'<input class="pulse-input" name="note" maxlength="{ev.MAX_NOTE}" aria-label="Anmerkung" placeholder="Anmerkung (optional)">'
               f'<button class="pulse-btn primary">Beweis hinzufügen</button></form>')
        body_parts.append(f'<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">📎 Beweise zu <code>{esc(data["ref"])}</code></div>'
                          f'<div class="pulse-section-sub">{esc(data["case"]["title"])} · {data["active_count"]} aktive Einträge · Entfernen bleibt nachvollziehbar</div></div></div>'
                          f'<div class="pulse-card-b space-y-2">{rows}{add}</div></section>')
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">📎 Fall-Beweise</div>'
            f'<div class="pulse-section-sub">Einheitliche Beweisverwaltung für alle Fallakten, für die du berechtigt bist.</div></div>'
            f'<a class="pulse-btn ghost" href="/mod-cases/verlauf">🗂 Gesamtverlauf</a></div>'
            f'<section class="pulse-card mb-4"><div class="pulse-card-b"><form method="get" class="flex flex-wrap gap-2" role="search">'
            f'<input class="pulse-input" name="ref" value="{esc(ref)}" aria-label="Fallreferenz" placeholder="Referenz, z. B. BM:MOD-1A2B3C">'
            f'<button class="pulse-btn primary">Öffnen</button></form></div></section>' + "".join(body_parts))
    import sys
    return HTMLResponse(render_pro_page(sys.modules["webserver"], "Fall-Beweise", ctx, "mod-cases", body))


def _evidence_redirect(ref: str, message: str):
    """Fixed local target; the reference is re-built from a validated (kind, id) pair, never echoed as given."""
    import pulse_case_registry as reg
    parsed = reg.parse_ref(ref)
    safe_ref = reg.make_ref(*parsed) if parsed else ""
    return RedirectResponse("/mod-cases/beweise?" + urlencode({"ref": safe_ref, "msg": message[:200]}), status_code=303)


@app.post("/mod-cases/beweise/add")
async def mod_case_evidence_add(request: Request, ref: str = Form(...), kind: str = Form(...), value: str = Form(...),
                                note: str = Form(""), user_session: str = Cookie(None)):
    import pulse_case_evidence as ev
    ctx = auth(request, user_session, perm=None)
    import pulse_case_registry
    if not pulse_case_registry.allowed_kinds(ctx.perms):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir ein Moderations-, Warn- oder Leitungsrecht.")
    actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
    try:
        item = ev.add_evidence(ref, ctx.perms, guild_id=getattr(ctx.guild, "id", None), kind=kind, value=value, note=note,
                               actor_id=ctx.user["id"], actor_name=actor)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Dafür fehlt dir das Recht für diese Fallart.")
    except ValueError as exc:
        return _evidence_redirect(ref, str(exc))
    log_audit(actor, ctx.user["id"], "Fall-Beweis hinzugefügt", f"{item['case_ref']} · {item['id']} · {item['kind']}")
    return _evidence_redirect(item["case_ref"], "Beweis hinzugefügt.")


@app.post("/mod-cases/beweise/remove")
async def mod_case_evidence_remove(request: Request, ref: str = Form(...), evidence_id: str = Form(...),
                                   reason: str = Form(...), user_session: str = Cookie(None)):
    import pulse_case_evidence as ev
    ctx = auth(request, user_session, perm=None)
    import pulse_case_registry
    if not pulse_case_registry.allowed_kinds(ctx.perms):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir ein Moderations-, Warn- oder Leitungsrecht.")
    actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
    try:
        row = ev.remove_evidence(evidence_id, ctx.perms, guild_id=getattr(ctx.guild, "id", None), reason=reason,
                                 actor_id=ctx.user["id"], actor_name=actor)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Beweis nicht gefunden.")
    except ValueError as exc:
        return _evidence_redirect(ref, str(exc))
    log_audit(actor, ctx.user["id"], "Fall-Beweis entfernt", f"{row['case_ref']} · {row['id']} · {reason[:100]}")
    return _evidence_redirect(row["case_ref"], "Beweis entfernt (bleibt im Verlauf sichtbar).")


@app.get("/mod-cases/stats", response_class=HTMLResponse)
async def mod_cases_stats_page(request: Request, user_session: str = Cookie(None)):
    """Audit #24: moderation statistics by type, result, duration and AutoMod quality (read-only, one guild)."""
    import pulse_mod_stats as ms
    from pulse_pro import render_pro_page
    ctx = _mod_case_ctx(request, user_session)
    days = ms.normalize_period(request.query_params.get("days"))
    stats = ms.collect(GUILD_ID, days)
    aria_current = ' aria-current="page"'
    periods = "".join(
        f'<a class="pulse-btn {"primary" if p == days else "ghost"}" href="/mod-cases/stats?days={p}"'
        f'{aria_current if p == days else ""}>{p} Tage</a>' for p in ms.PERIODS)
    head = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">📊 Moderationsstatistik</div>'
            f'<div class="pulse-section-sub">Auswertung der /mod-Fälle und AutoMod-Maßnahmen dieses Servers. '
            f'Nur gespeicherte Daten; Testtreffer zählen nicht als Maßnahme.</div></div>'
            f'<div class="flex flex-wrap gap-2" role="group" aria-label="Zeitraum">{periods}'
            f'<a class="pulse-btn ghost" href="/mod-cases">⚖ Fallakte</a>'
            f'<a class="pulse-btn ghost" href="/mod-cases/stats.csv?days={days}">⬇ CSV</a></div></div>')
    if not stats.get("available"):
        body = head + f'<section class="pulse-card"><div class="pulse-card-b">{empty_state(stats.get("reason", "Keine Daten."), "⚠")}</div></section>'
    elif not stats["has_data"]:
        body = head + f'<section class="pulse-card"><div class="pulse-card-b">{empty_state(f"In den letzten {days} Tagen gab es keine Moderationsfälle und keine AutoMod-Treffer.", "📊")}</div></section>'
    else:
        c, a = stats["cases"], stats["automod"]

        def delta(value):
            return "±0" if value == 0 else f"{value:+d}"
        tiles = [("⚖", "Fälle", c["total"], f"{delta(stats['cases_delta'])} zum Vorzeitraum"),
                 ("⏱", "Median bis Abschluss", _mod_stats_value(c["median_hours_to_close"], " h"), f"{c['closed_count']} abgeschlossen"),
                 ("✉", "DM zugestellt", _mod_stats_value(c["dm_rate"], " %"), "der Fälle"),
                 ("📎", "Mit Beweis", _mod_stats_value(c["proof_rate"], " %"), f"Vorlage: {_mod_stats_value(c['template_rate'], ' %')}"),
                 ("🔁", "Wiederholungen", c["repeat_people"], f"von {c['affected_people']} Personen"),
                 ("🤖", "AutoMod-Maßnahmen", a["total"], f"{delta(stats['automod_delta'])} · {a['tests']} Testtreffer"),
                 ("⚠", "Fehlalarmquote", _mod_stats_value(a["false_positive_rate"], " %"), f"{a['false_positives']} geprüft")]
        grid = "".join(f'<div class="pulse-stat"><div class="icon" aria-hidden="true">{i}</div><div class="label">{esc(lbl)}</div>'
                       f'<div class="value">{esc(v)}</div><div class="pulse-row-meta">{esc(sub)}</div></div>' for i, lbl, v, sub in tiles)
        peak = max((max(n, m) for _d, n, m in stats["daily"]), default=0) or 1
        trend = "".join(
            f'<div title="{esc(d)}: {n} Fälle, {m} AutoMod" style="flex:1;display:flex;gap:1px;align-items:flex-end;height:100%">'
            f'<span style="flex:1;height:{round(n * 100 / peak)}%;background:var(--p);border-radius:2px 2px 0 0;min-height:{1 if n else 0}px"></span>'
            f'<span style="flex:1;height:{round(m * 100 / peak)}%;background:var(--muted);opacity:.55;border-radius:2px 2px 0 0;min-height:{1 if m else 0}px"></span></div>'
            for d, n, m in stats["daily"])
        trend_card = (f'<section class="pulse-card mb-4"><div class="pulse-card-h"><div><div class="pulse-section-title">Verlauf je Tag</div>'
                      f'<div class="pulse-section-sub">Orange: /mod-Fälle · Grau: AutoMod-Maßnahmen</div></div></div>'
                      f'<div class="pulse-card-b"><div role="img" aria-label="Tagesverlauf der letzten {days} Tage" '
                      f'style="display:flex;gap:2px;align-items:flex-end;height:110px">{trend}</div></div></section>')
        by_type = _mod_stats_bars([(lbl, n, "") for _k, lbl, n in c["by_action"]], c["total"])
        by_result = _mod_stats_bars([(lbl, n, "") for _k, lbl, n in c["by_status"]]
                                    + ([("sonstiger Status", c["other_status"], "")] if c["other_status"] else []), c["total"])
        rules = _mod_stats_bars([(rule, n, f" · {fp} Fehlalarm{'e' if fp != 1 else ''}") for rule, n, fp in a["by_rule"]], a["total"]) \
            or empty_state("Keine AutoMod-Maßnahmen im Zeitraum.", "🤖")
        mods = "".join(f'<div class="pulse-row"><div class="pulse-row-title">{esc(name)}</div><span class="pulse-pill">{n}</span></div>'
                       for name, n in c["moderators"]) or empty_state("Keine Fälle im Zeitraum.", "⚖")

        def card(title, sub, inner):
            return (f'<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">{esc(title)}</div>'
                    f'<div class="pulse-section-sub">{esc(sub)}</div></div></div><div class="pulse-card-b">{inner}</div></section>')
        body = (head + f'<div class="pulse-stat-grid">{grid}</div>' + trend_card
                + '<div class="pulse-two mb-4">' + card("Nach Maßnahme", "Anteil an allen /mod-Fällen", by_type)
                + card("Nach Ergebnis", f"Fehlgeschlagen: {_mod_stats_value(c['failure_rate'], ' %')}", by_result) + '</div>'
                + '<div class="pulse-two">' + card("AutoMod nach Regel", f"Median bis Fehlalarmprüfung: {_mod_stats_value(a['median_hours_to_review'], ' h')}", rules)
                + card("Fälle je Moderator", "Top 10 im Zeitraum – Menge, keine Leistungsbewertung", f'<div class="pulse-list">{mods}</div>') + '</div>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules["webserver"], "Moderationsstatistik", ctx, "mod-stats", body))


@app.get("/mod-cases/stats.csv")
async def mod_cases_stats_csv(request: Request, user_session: str = Cookie(None)):
    import pulse_lists
    import pulse_mod_stats as ms
    ctx = _mod_case_ctx(request, user_session)
    days = ms.normalize_period(request.query_params.get("days"))
    stats = ms.collect(GUILD_ID, days)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Moderationsstatistik exportiert", f"{days} Tage")
    return pulse_lists.csv_response(f"pulse_moderationsstatistik_{now_de().strftime('%Y-%m-%d')}.csv",
                                    ["Kennzahl", "Wert", "Vergleich/Hinweis"], ms.csv_rows(stats))


@app.get("/tickets/tools", response_class=HTMLResponse)
async def pulse_ticket_tools(request: Request, user_session: str = Cookie(None)):
    """BOT-06: quick replies (admins edit), ticket bans (ticket team), rating report."""
    import pulse_ticket_extras as tx
    from pulse_pro import render_pro_page
    ctx = auth(request, user_session, perm="can_manage_tickets")
    is_admin = bool(ctx.perms.get("is_admin"))
    try:
        from cogs.tickets import category_meta, ticket_category_definitions
        categories = [(key, category_meta(key)[0]) for key in ticket_category_definitions()]
    except Exception:
        categories = []
    reply_blocks = "".join(
        f'<label class="grid gap-1 text-xs font-semibold">{esc(label)}'
        f'<textarea class="pulse-input pulse-textarea" name="replies_{esc(key)}" rows="3" placeholder="Titel | Text (eine Antwort pro Zeile)"'
        f'{"" if is_admin else " readonly"}>{esc(tx.replies_as_text(key))}</textarea></label>'
        for key, label in [("*", "Für alle Kategorien")] + categories)
    replies_form = (f'<form method="post" action="/tickets/tools/replies" class="space-y-3">{reply_blocks}'
                    + ('<button class="pulse-btn primary" type="submit">💾 Schnellantworten speichern</button>' if is_admin
                       else '<div class="text-xs text-slate-400">Bearbeiten können nur Admins.</div>') + '</form>')
    blocks = tx.load_blocks()
    block_rows = "".join(
        f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><code>{esc(uid)}</code>'
        f'{" · " + esc(getattr(ctx.guild.get_member(int(uid)), "display_name", "")) if str(uid).isdigit() and ctx.guild.get_member(int(uid)) else ""}</div>'
        f'<div class="pulse-row-meta">bis {esc(str(entry.get("until"))[:10])} · {esc(entry.get("reason"))} · von {esc(entry.get("by"))}</div></div>'
        f'<form method="post" action="/tickets/tools/unblock"><input type="hidden" name="user_id" value="{esc(uid)}">'
        f'<button class="pulse-btn ghost" type="submit">Aufheben</button></form></div>'
        for uid, entry in blocks.items() if tx.block_for_entry(entry)) or '<div class="pulse-empty">Keine aktiven Ticket-Sperren. Setzen: /ticket sperre im Discord.</div>'
    report = tx.ratings_report(pulse_db.list_tickets(status="closed", limit=2000))
    names = dict(categories)

    def table(rows, label_of=lambda k: k):
        return "".join(f'<div class="pulse-row"><div class="pulse-row-title">{esc(label_of(k))}</div>'
                       f'<div class="pulse-row-meta">⭐ {avg:.2f} · {n} Bewertungen</div></div>' for k, avg, n in rows[:20]) \
            or '<div class="pulse-empty">Noch keine Bewertungen.</div>'
    rating_html = (f'<div class="text-xs mb-3">Gesamt: {"⭐ %.2f" % report["average"] if report["average"] else "–"} aus {report["count"]} Bewertungen</div>'
                   f'<div class="grid lg:grid-cols-2 gap-4"><div><div class="pulse-section-title mb-2">Nach Kategorie</div>{table(report["by_category"], lambda k: names.get(k, k))}</div>'
                   f'<div><div class="pulse-section-title mb-2">Nach Teammitglied</div>{table(report["by_staff"])}</div></div>')
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">🧰 Ticket-Werkzeuge</div>'
            f'<div class="pulse-section-sub">Schnellantworten für /ticket antwort, Ticket-Sperren und die Auswertung der Bewertungen.</div></div>'
            f'<a class="pulse-btn ghost" href="/tickets">← Tickets</a></div>'
            f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">💬 Schnellantworten</div></div><div class="pulse-card-b">{replies_form}</div></section>'
            f'<section class="pulse-card mt-4"><div class="pulse-card-h"><div class="pulse-section-title">🚫 Ticket-Sperren</div></div><div class="pulse-card-b space-y-2">{block_rows}</div></section>'
            f'<section class="pulse-card mt-4"><div class="pulse-card-h"><div class="pulse-section-title">⭐ Bewertungen</div></div><div class="pulse-card-b">{rating_html}</div></section>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules["webserver"], "Ticket-Werkzeuge", ctx, "tickets", body))


@app.post("/tickets/tools/replies")
async def pulse_ticket_tools_replies(request: Request, user_session: str = Cookie(None)):
    import pulse_ticket_extras as tx
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()
    try:
        from cogs.tickets import ticket_category_definitions
        keys = ["*", *ticket_category_definitions().keys()]
    except Exception:
        keys = ["*"]
    saved = tx.save_replies({key: form.get(f"replies_{key}", "") for key in keys})
    import pulse_ticket_replies  # F8: every save also becomes a version in the template library (never blocks saving)
    pulse_ticket_replies.sync_from_legacy(ctx.perms, ctx.guild.id, ctx.user["id"],
                                          ctx.user.get("global_name") or ctx.user.get("username") or "Admin", saved)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Ticket-Schnellantworten geändert",
              f"{sum(len(v) for v in saved.values())} Antworten")
    return back("/tickets/tools", "Schnellantworten gespeichert.")


@app.post("/tickets/tools/unblock")
async def pulse_ticket_tools_unblock(request: Request, user_session: str = Cookie(None)):
    import pulse_ticket_extras as tx
    ctx = auth(request, user_session, perm="can_manage_tickets")
    user_id = str((await request.form()).get("user_id") or "")
    if not tx.unblock(user_id):
        return back("/tickets/tools", "Für diese Person gab es keine Sperre.", False)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Ticket-Sperre aufgehoben", user_id)
    return back("/tickets/tools", "Ticket-Sperre aufgehoben.")


@app.post("/tickets/action")
async def pulse_tickets_dashboard_action(
    request: Request,
    ticket_id: str = Form(...),
    action: str = Form(...),
    reason: str = Form(""),
    priority: str = Form("normal"),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm="can_manage_tickets")
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not pulse_ticket_access.can_access_ticket(ctx, ticket):
        return back("/tickets", "Ticket nicht gefunden oder gehört zu einem anderen Server.", False)

    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets", "Pulse-Bot ist nicht erreichbar.", False)
    actor_id = int(ctx.member.id)
    actor_name = ctx.member.display_name
    channel_id = int(ticket.get("channel_id") or 0)
    channel = ctx.guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            channel = None

    if action == "reopen":
        if ticket.get("status") != "closed":
            return back("/tickets", "Nur geschlossene Tickets können wieder geöffnet werden.", False)
        from pulse_ticket_design import reopen_allowed
        allowed, refusal = reopen_allowed(
            ticket, pulse_ticket_config().get("reopen"), datetime.now(timezone.utc),
            is_admin=bool(ctx.perms.get("is_admin")),
        )
        if not allowed:
            return back("/tickets?status=closed", refusal, False)
        try:
            from cogs.tickets import TICKET_PARENT_NAME, TicketControlView, category_channel_name, category_meta, category_roles, safe_channel_name, ticket_category_definitions
            owner = ctx.guild.get_member(int(ticket.get("user_id") or 0))
            if owner is None:
                try:
                    owner = await ctx.guild.fetch_member(int(ticket.get("user_id") or 0))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    owner = None
            parent = discord.utils.get(ctx.guild.categories, name=TICKET_PARENT_NAME)
            if parent is None:
                parent = await ctx.guild.create_category(TICKET_PARENT_NAME, reason="Pulse-Ticket erneut geöffnet")
            roles = category_roles(ctx.guild, str(ticket.get("category") or "support"))
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, manage_permissions=True, attach_files=True, embed_links=True),
            }
            if owner:
                overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
            for role in roles:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True, attach_files=True, embed_links=True)
            short_id = re.sub(r"[^a-zA-Z0-9]", "", str(ticket.get("id") or ""))[-8:] or "ticket"
            channel = await ctx.guild.create_text_channel(
                category_channel_name(ticket_category_definitions().get(str(ticket.get("category") or "support"), {}),
                                      getattr(owner, "name", None) or str(ticket.get("user_name") or "user"),
                                      f"-r{short_id[-4:].lower()}"),
                category=parent,
                overwrites=overwrites,
                topic=f"Pulse Ticket | ticket:{ticket_id} | user:{ticket.get('user_id')} | category:{ticket.get('category')}",
                reason=f"Pulse-Ticket erneut geöffnet von {actor_name}",
            )
            if not pulse_db.reopen_ticket(ticket_id, channel.id, actor_id, actor_name):
                await channel.delete(reason="Ticket-Datenbank hat Wiederöffnung abgelehnt")
                return back("/tickets?status=closed", "Ticket konnte nicht wieder geöffnet werden.", False)
            updated = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
            label, _, emoji, _, _ = category_meta(str(updated.get("category") or "support"))
            embed = discord.Embed(
                title=f"{emoji} {label} – Ticket erneut geöffnet",
                description=f"Dieses Ticket wurde von {ctx.member.mention} erneut geöffnet. Der bisherige Verlauf bleibt in der Transcript-Datei erhalten.",
                color=_pulse_color("success"),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Ticket-ID", value=str(ticket_id), inline=True)
            embed.add_field(name="Ersteller", value=str(updated.get("user_name") or "Unbekannt"), inline=True)
            embed.add_field(name="Status", value="🟢 Offen", inline=True)
            await send_styled(channel, embed=embed, view=TicketControlView(), allowed_mentions=discord.AllowedMentions.none())
            pulse_db.record_event("ticket_reopened_dashboard", "ticket", ticket_id, actor_id, actor_name, {"channel_id": channel.id},
                              correlation_id=f"ticket:{ticket_id}")
            await _send_pulse_ticket_log(bot, ctx.guild, "reopened", updated, ctx.member)
            return back("/tickets?status=open", f"Ticket {ticket_id} wurde wieder geöffnet.")
        except (discord.Forbidden, discord.HTTPException, ImportError) as exc:
            print(f"⚠️ Pulse-Tickets: Wiederöffnung fehlgeschlagen ({type(exc).__name__}).")
            return back("/tickets?status=closed", "Ticket konnte nicht wieder geöffnet werden. Prüfe die Bot-Rechte.", False)

    if ticket.get("status") == "closed":
        return back("/tickets?status=closed", "Dieses Ticket ist bereits geschlossen.", False)

    if action == "claim":
        if not pulse_db.claim_ticket(ticket["id"], actor_id, actor_name):
            latest = pulse_db.get_ticket(ticket_id=ticket_id)
            who = latest.get("claimed_by_name") if latest else "jemand anderem"
            return back("/tickets?status=claimed", f"Ticket wurde bereits von {who} übernommen.", False)
        latest = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        pulse_db.record_event("ticket_claimed_dashboard", "ticket", ticket_id, actor_id, actor_name, {"channel_id": channel_id},
                          correlation_id=f"ticket:{ticket_id}")
        try:
            from cogs.tickets import TicketControlView, refresh_ticket_embed
            await _refresh_pulse_ticket_discord_state(channel, latest)
        except Exception:
            pass
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"📥 {ctx.member.mention} hat dieses Ticket über das Pulse-Dashboard übernommen.", allowed_mentions=discord.AllowedMentions(users=[ctx.member], roles=False, everyone=False))
            except (discord.Forbidden, discord.HTTPException):
                pass
        await _send_pulse_ticket_log(bot, ctx.guild, "claimed", latest, ctx.member)
        return back("/tickets?status=claimed", f"Ticket {ticket_id} wurde übernommen.")

    if action == "unclaim":
        if str(ticket.get("claimed_by_id") or "") != str(actor_id) and not ctx.perms.get("is_admin"):
            return back("/tickets?status=claimed", "Du darfst nur deine eigenen Tickets freigeben (Admins dürfen alle freigeben).", False)
        if not pulse_db.unclaim_ticket(ticket["id"], actor_id, actor_name):
            return back("/tickets?status=claimed", "Ticket konnte nicht freigegeben werden.", False)
        latest = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        await _refresh_pulse_ticket_discord_state(channel, latest)
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"{actor_name} hat das Ticket im Pulse-Dashboard freigegeben.", allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
        await _send_pulse_ticket_log(bot, ctx.guild, "unclaimed", latest, ctx.member)
        return back("/tickets?status=open", f"Ticket {ticket_id} wurde freigegeben.")

    if action == "priority":
        if priority not in PULSE_TICKET_PRIORITY_LABELS:
            return back("/tickets", "Ungültige Ticketpriorität.", False)
        if not pulse_db.update_ticket_priority(ticket_id, priority, actor_id, actor_name):
            return back("/tickets", "Priorität konnte nicht geändert werden.", False)
        latest = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        await _refresh_pulse_ticket_discord_state(channel, latest)
        pulse_db.record_event("ticket_priority_dashboard", "ticket", ticket_id, actor_id, actor_name, {"priority": priority},
                          correlation_id=f"ticket:{ticket_id}")
        await _send_pulse_ticket_log(bot, ctx.guild, "priority", latest, ctx.member)
        return back(f"/tickets?status={('claimed' if latest.get('claimed_by_id') else 'open')}", f"Priorität für Ticket {ticket_id} geändert auf {PULSE_TICKET_PRIORITY_LABELS[priority]}.")

    if action == "close":
        reason = str(reason or "").strip()[:500]
        if not reason:
            return back("/tickets", "Beim Schließen ist ein Grund erforderlich.", False)
        if not ctx.perms.get("is_admin") and not ctx.perms.get("can_manage_tickets"):
            return back("/tickets", "Dafür fehlt dir die Berechtigung.", False)
        if not isinstance(channel, discord.TextChannel):
            return back("/tickets", "Der Ticketkanal ist nicht erreichbar; das Ticket wurde nicht verändert.", False)
        # K-06: without a complete transcript the channel is kept and the ticket stays open.
        from pulse_transcripts import TranscriptError, archive_transcript, keep_channel_after_failure
        log_channel = ctx.guild.get_channel(pulse_ticket_log_channel_id("closed"))
        try:
            transcript_name = await archive_transcript(channel, ticket["id"], log_channel=log_channel)
        except TranscriptError as exc:
            pulse_db.record_event("ticket_archive_failed", "ticket", ticket_id, actor_id, actor_name, {"error": str(exc)},
                              correlation_id=f"ticket:{ticket_id}")
            await keep_channel_after_failure(channel, ticket, str(exc))
            return back("/tickets", f"Ticket {ticket_id} nicht geschlossen: {exc}. Der Kanal bleibt erhalten.", False)
        if not pulse_db.close_ticket(ticket["id"], reason, transcript_name, actor_id, actor_name):
            return back("/tickets", "Ticket konnte nicht geschlossen werden.", False)
        closed = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        pulse_db.record_event("ticket_closed_dashboard", "ticket", ticket_id, actor_id, actor_name, {"reason": reason},
                          correlation_id=f"ticket:{ticket_id}")
        await _send_pulse_ticket_log(bot, ctx.guild, "closed", closed, ctx.member)
        try:
            owner = ctx.guild.get_member(int(ticket.get("user_id") or 0))
            if owner:
                dm = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", color=_pulse_color("info"))
                dm.description = f"Ticket {ticket_id} wurde von {actor_name} geschlossen.\n\nGrund: {reason}"
                await send_styled(owner, embed=dm)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            pass
        try:
            await channel.delete(reason=f"Pulse Ticket geschlossen aus dem Dashboard von {actor_name}")
        except (discord.Forbidden, discord.HTTPException):
            pass
        return back("/tickets?status=closed", f"Ticket {ticket_id} wurde geschlossen.")

    return back("/tickets", "Unbekannte Ticketaktion.", False)


# =============================================================
# ROUTE 2: TEAMLISTE (Wochenziel mit Fortschrittsbalken)
# =============================================================
def format_support_case_period_start(value: str) -> str:
    """Format the stored UTC period boundary in Pulse's configured local timezone."""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(TZ)
        return stamp.strftime("%d.%m.%Y %H:%M Uhr")
    except (TypeError, ValueError, OverflowError):
        return str(value or "unbekannt")


def active_voice_support_case_counts(guild_id: int) -> dict[str, int]:
    """Count currently open voice-support cases by their assigned staff member."""
    cases = pulse_db.get_setting("voice_support_cases", {})
    counts: dict[str, int] = {}
    if not isinstance(cases, dict):
        return counts
    for case in cases.values():
        if not isinstance(case, dict) or str(case.get("guild_id", "")) != str(guild_id):
            continue
        staff_id = str(case.get("assigned_staff_id") or "").strip()
        if not staff_id or staff_id == "0":
            continue
        counts[staff_id] = counts.get(staff_id, 0) + 1
    return counts


