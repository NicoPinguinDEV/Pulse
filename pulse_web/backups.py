"""Discord structure backups, full backups, restore workflows and their routes."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

# =============================================================
# DISCORD BACKUP SYSTEM (nur Admins, mit Auto-Backup)
# =============================================================
BACKUP_NAME_RE = re.compile(r"^(?:(?:auto_|pre_restore_|uploaded_))?backup_[0-9_\-]+\.json$")


def safe_backup_path(filename: str):
    """Verhindert Path-Traversal (vorher: filename=../../.env möglich)."""
    name = os.path.basename(filename or "")
    if not BACKUP_NAME_RE.fullmatch(name):
        return None
    path = contained_file(BACKUP_DIR, name)
    return path if path and os.path.isfile(path) else None


def _overwrites(channel):
    out = []
    for target, ow in channel.overwrites.items():
        allow, deny = ow.pair()
        out.append({
            "type": "role" if isinstance(target, discord.Role) else "member",
            "id": int(target.id),
            "name": getattr(target, "name", str(target)),
            "allow": int(allow.value),
            "deny": int(deny.value),
        })
    return out


def _enum_int(value, default=None):
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _channel_dict(ch):
    """Serialize restorable channel structure without message history."""
    data = {
        "id": int(ch.id),
        "name": str(ch.name),
        "type": str(ch.type),
        "topic": getattr(ch, "topic", None),
        "position": int(getattr(ch, "position", 0) or 0),
        "nsfw": getattr(ch, "nsfw", None),
        "slowmode": getattr(ch, "slowmode_delay", None),
        "overwrites": _overwrites(ch),
        "category_id": int(ch.category_id) if getattr(ch, "category_id", None) else None,
        "bitrate": getattr(ch, "bitrate", None),
        "user_limit": getattr(ch, "user_limit", None),
        "rtc_region": getattr(ch, "rtc_region", None),
        "video_quality_mode": _enum_int(getattr(ch, "video_quality_mode", None)),
        "default_auto_archive_duration": getattr(ch, "default_auto_archive_duration", None),
        "default_thread_slowmode_delay": getattr(ch, "default_thread_slowmode_delay", None),
        "default_sort_order": _enum_int(getattr(ch, "default_sort_order", None)),
        "default_layout": _enum_int(getattr(ch, "default_layout", None)),
    }
    available_tags = getattr(ch, "available_tags", None)
    if available_tags is not None:
        data["available_tags"] = [
            {
                "name": str(tag.name),
                "moderated": bool(getattr(tag, "moderated", False)),
                "emoji_name": getattr(getattr(tag, "emoji", None), "name", None),
                "emoji_id": getattr(getattr(tag, "emoji", None), "id", None),
            }
            for tag in available_tags
        ]
    return data


def build_backup(guild, guild_icon_base64: str | None = None) -> dict:
    """Build a JSON-safe snapshot of Pulse data and Discord's restorable structure."""
    return add_panel_data(build_discord_structure(guild, guild_icon_base64))


def build_discord_structure(guild, guild_icon_base64: str | None = None) -> dict:
    """D-03: Discord part of the backup – reads discord.py caches, so it runs in the event loop."""
    icon = getattr(guild, "icon", None)
    data = {
        "backup_version": 3,
        "pulse_version": PULSE_VERSION,
        "guild_name": guild.name,
        "guild_id": int(guild.id),
        "created_at": now_de().strftime("%Y-%m-%d_%H-%M-%S"),
        "guild_settings": {
            "name": guild.name,
            "description": getattr(guild, "description", None),
            "verification_level": _enum_int(getattr(guild, "verification_level", None)),
            "default_notifications": _enum_int(getattr(guild, "default_notifications", None)),
            "explicit_content_filter": _enum_int(getattr(guild, "explicit_content_filter", None)),
            "afk_timeout": _enum_int(getattr(guild, "afk_timeout", None)),
            "preferred_locale": str(getattr(guild, "preferred_locale", "") or ""),
            "premium_progress_bar_enabled": getattr(guild, "premium_progress_bar_enabled", None),
            "system_channel_id": getattr(getattr(guild, "system_channel", None), "id", None),
            "rules_channel_id": getattr(getattr(guild, "rules_channel", None), "id", None),
            "public_updates_channel_id": getattr(getattr(guild, "public_updates_channel", None), "id", None),
            "afk_channel_id": getattr(getattr(guild, "afk_channel", None), "id", None),
            "system_channel_flags": _enum_int(getattr(guild, "system_channel_flags", None)),
            "icon_base64": guild_icon_base64,
            "icon_key": getattr(icon, "key", None),
            "icon_restored_from_backup": bool(guild_icon_base64),
        },
        "roles": [],
        "categories": [],
        "uncategorized_channels": [],
        "member_assignments": [],
        "panel_data": {},
    }

    for role in guild.roles:
        if role.is_default():
            continue
        data["roles"].append({
            "id": int(role.id),
            "name": str(role.name),
            "color": int(role.color.value),
            "permissions": int(role.permissions.value),
            "hoist": bool(role.hoist),
            "mentionable": bool(role.mentionable),
            "position": int(role.position),
            "managed": bool(role.managed),
            "unicode_emoji": getattr(role, "unicode_emoji", None),
        })

    for category in guild.categories:
        data["categories"].append({
            "id": int(category.id),
            "name": str(category.name),
            "position": int(category.position),
            "overwrites": _overwrites(category),
            "channels": [_channel_dict(ch) for ch in category.channels],
        })

    data["uncategorized_channels"] = [
        _channel_dict(ch) for ch in guild.channels
        if ch.category is None and not isinstance(ch, discord.CategoryChannel)
    ]

    # Membership is never recreated or kicked. Existing members' role assignments
    # and server nicknames can, however, be restored by a privileged administrator.
    data["member_assignments"] = [
        {
            "id": int(member.id),
            "nick": member.nick,
            "role_ids": [int(role.id) for role in member.roles if not role.is_default()],
        }
        for member in guild.members
    ]

    return data


def add_panel_data(data: dict) -> dict:
    """D-03: file/database part of the backup – no Discord objects, safe in a worker thread."""
    backup_defaults = {
        "team_data": {}, "config": {}, "logs": [],
        "shifts": {}, "meetings": {}, "applications": {},
    }
    for key, path in (("team_data", DATA_FILE), ("config", CONFIG_FILE),
                      ("shifts", SHIFTS_FILE), ("meetings", MEETINGS_FILE), ("applications", APPS_FILE)):
        data["panel_data"][key] = load_json(path, backup_defaults[key])

    # The legacy server-structure backup stores an exported view of the active
    # Melonly repository. It must never serialize a stale logs.json in SQLite mode.
    melonly_repository = get_melonly_repository()
    with _io_lock:
        data["panel_data"]["logs"] = melonly_repository.export_all()
        data["panel_data"]["melonly_storage"] = {
            "backend": melonly_repository.backend_name,
            "metadata": (
                melonly_repository.metadata()
                if melonly_repository.backend_name == "sqlite" else {}
            ),
        }

    try:
        data["panel_data"]["pulse_db"] = pulse_db.export_state()
    except Exception as exc:
        # Fail closed rather than claiming this backup contains a database that
        # could not be read. The UI surfaces this as a warning to the operator.
        data["panel_data"]["pulse_db_error"] = f"{type(exc).__name__}: {exc}"

    # The Activity Check database includes immutable participant snapshots and
    # warning idempotency state, so it must travel with the backup as well.
    sqlite_files = {}
    for db_name in ("activity_check.db",):
        db_path = str(DATA_DIR / db_name)
        if not os.path.isfile(db_path):
            continue
        try:
            with closing(sqlite3.connect(db_path, timeout=3.0)) as checkpoint_db, checkpoint_db:
                checkpoint_db.execute("PRAGMA wal_checkpoint(FULL)")
            if os.path.getsize(db_path) <= 20 * 1024 * 1024:
                with open(db_path, "rb") as fh:
                    sqlite_files[db_name] = base64.b64encode(fh.read()).decode("ascii")
        except (OSError, sqlite3.Error):
            continue
    data["panel_data"]["sqlite_files_base64"] = sqlite_files

    # Ticket transcripts are included within a combined size budget, preventing
    # a large transcript directory from exhausting memory during backup creation.
    transcripts = {}
    transcript_bytes = 0
    transcript_dir = os.path.join(DATA_DIR, "transcripts")
    if os.path.isdir(transcript_dir):
        for name in sorted(os.listdir(transcript_dir)):
            path = os.path.join(transcript_dir, name)
            if not (name.endswith(".txt") and os.path.isfile(path)):
                continue
            try:
                size = os.path.getsize(path)
                if size > 2 * 1024 * 1024 or transcript_bytes + size > 10 * 1024 * 1024:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    transcripts[name] = fh.read()
                transcript_bytes += size
            except OSError:
                continue
    data["panel_data"]["transcripts"] = transcripts

    try:
        with pulse_sqlite.connect_readonly(DB_ABMELDUNGEN, timeout=3.0) as conn:
            rows = conn.execute(
                "SELECT user_id,user_name,grund,von,bis,original_nick,guild_id FROM abmeldungen"
            ).fetchall()
        data["panel_data"]["loas"] = [
            dict(zip(["user_id", "user_name", "grund", "von", "bis", "original_nick", "guild_id"], row))
            for row in rows
        ]
    except Exception:
        data["panel_data"]["loas"] = []

    return data


def ensure_panel_backup_restore_backend_compatible(repository) -> None:
    """Prevent the legacy JSON/Discord-structure restore from overwriting live SQLite Melonly."""
    if getattr(repository, "backend_name", "json") == "sqlite":
        raise ValueError(
            "Das Serverstruktur-Backup kann das aktive SQLite-Melonly-Schema nicht wiederherstellen. "
            "Bitte das geprüfte vollständige ZIP-Backup verwenden."
        )


def validate_backup_document(backup: dict, guild_id: int | None = None) -> None:
    """Validate imported/local backup shape before it can touch runtime data."""
    if not isinstance(backup, dict) or not isinstance(backup.get("panel_data"), dict):
        raise ValueError("Kein gültiges Pulse-Backup: Panel-Daten fehlen.")
    try:
        version = int(backup.get("backup_version", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültige Backup-Version.") from exc
    if version < 1 or version > 3:
        raise ValueError(f"Nicht unterstützte Backup-Version: {version}")
    stored_guild_id = backup.get("guild_id")
    if guild_id is not None and str(stored_guild_id) != str(guild_id):
        raise ValueError("Dieses Backup gehört zu einem anderen Discord-Server; Wiederherstellung abgebrochen.")

    panel = backup["panel_data"]
    expected_types = {
        "team_data": dict, "config": dict, "logs": list,
        "shifts": dict, "meetings": dict, "applications": dict,
        "pulse_db": dict, "loas": list, "transcripts": dict,
        "sqlite_files_base64": dict, "melonly_storage": dict,
    }
    if "melonly_storage" in panel:
        storage = panel["melonly_storage"]
        if storage.get("backend") not in {"json", "sqlite"}:
            raise ValueError("Ungültiges Melonly-Backend im Server-Backup.")
        if not isinstance(storage.get("metadata", {}), dict):
            raise ValueError("Ungültige Melonly-Metadaten im Server-Backup.")
    if panel.get("logs") == {}:
        panel["logs"] = []
    for key, expected_type in expected_types.items():
        if key in panel and not isinstance(panel[key], expected_type):
            raise ValueError(f"Ungültiger Datentyp im Backup-Feld: {key}")

    # Bound collection counts before any API calls.
    roles = backup.get("roles", [])
    categories = backup.get("categories", [])
    channels = backup.get("uncategorized_channels", [])
    members = backup.get("member_assignments", [])
    settings = backup.get("guild_settings", {})
    if not isinstance(roles, list) or len(roles) > 250:
        raise ValueError("Ungültige Rollenliste im Backup.")
    if not isinstance(categories, list) or len(categories) > 500:
        raise ValueError("Ungültige Kategorienliste im Backup.")
    if not isinstance(channels, list) or len(channels) > 5000:
        raise ValueError("Ungültige Kanalliste im Backup.")
    if not isinstance(members, list) or len(members) > 100000:
        raise ValueError("Ungültige Mitgliederliste im Backup.")
    if not isinstance(settings, dict):
        raise ValueError("Ungültige Servereinstellungen im Backup.")
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("name"), str) or not role["name"] or len(role["name"]) > 100:
            raise ValueError("Ungültiger Rolleneintrag im Backup.")
    total_channels = len(channels)
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("name"), str):
            raise ValueError("Ungültiger Kategorieeintrag im Backup.")
        category_channels = category.get("channels", [])
        if not isinstance(category_channels, list) or len(category_channels) > 5000:
            raise ValueError("Ungültige Kanalliste in einer Kategorie.")
        total_channels += len(category_channels)
        if any(not isinstance(channel, dict) or not isinstance(channel.get("name"), str) for channel in category_channels):
            raise ValueError("Ungültiger Kanal in einer Kategorie.")
    if total_channels > 5000:
        raise ValueError("Das Backup enthält zu viele Kanäle für eine sichere Wiederherstellung.")
    if any(not isinstance(channel, dict) or not isinstance(channel.get("name"), str) for channel in channels):
        raise ValueError("Ungültiger nicht kategorisierter Kanal im Backup.")
    if any(not isinstance(member, dict) or not str(member.get("id", "")).isdigit() for member in members):
        raise ValueError("Ungültiger Mitgliedseintrag im Backup.")
    for role in roles:
        if not str(role.get("id", "")).isdigit():
            raise ValueError("Ungültige Rollen-ID im Backup.")
        for key in ("color", "permissions", "position"):
            if key in role and not isinstance(role[key], (int, float)):
                raise ValueError(f"Ungültiger Rollenwert: {key}.")
    for category in categories:
        if "id" in category and not str(category.get("id", "")).isdigit():
            raise ValueError("Ungültige Kategorie-ID im Backup.")
        for channel in category.get("channels", []):
            if "id" in channel and not str(channel.get("id", "")).isdigit():
                raise ValueError("Ungültige Kanal-ID im Backup.")
            if "overwrites" in channel and not isinstance(channel["overwrites"], list):
                raise ValueError("Ungültige Kanalrechte im Backup.")
    for channel in channels:
        if "id" in channel and not str(channel.get("id", "")).isdigit():
            raise ValueError("Ungültige Kanal-ID im Backup.")
        if "overwrites" in channel and not isinstance(channel["overwrites"], list):
            raise ValueError("Ungültige Kanalrechte im Backup.")
    for member in members:
        role_ids = member.get("role_ids", [])
        if not isinstance(role_ids, list) or any(not str(role_id).isdigit() for role_id in role_ids):
            raise ValueError("Ungültige Rollenliste eines Mitglieds im Backup.")
    sqlite_files = panel.get("sqlite_files_base64", {})
    if isinstance(sqlite_files, dict) and any(key != "activity_check.db" for key in sqlite_files):
        raise ValueError("Das Backup enthält eine nicht zulässige Datenbankdatei.")


def prune_auto_backups():
    autos = sorted(
        (name for name in os.listdir(BACKUP_DIR) if name.startswith("auto_") and name.endswith(".json")),
        key=lambda name: os.path.getmtime(os.path.join(BACKUP_DIR, name)),
        reverse=True,
    )
    for old in autos[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def create_backup_file(guild, prefix: str = "") -> str:
    """Synchronous compatibility helper; new UI/automatic backups use the async version."""
    filename = f"{prefix}backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S_%f')}.json"
    save_json(os.path.join(BACKUP_DIR, filename), build_backup(guild))
    if prefix == "auto_":
        prune_auto_backups()
    return filename


async def create_backup_file_async(guild, prefix: str = "") -> str:
    """Create a richer snapshot, including server icon bytes when Discord allows it."""
    icon_base64 = None
    icon = getattr(guild, "icon", None)
    if icon is not None:
        try:
            image_bytes = await asyncio.wait_for((icon.replace(format="png") if hasattr(icon, "replace") else icon).read(), timeout=10.0)
            if image_bytes and len(image_bytes) <= 8 * 1024 * 1024:
                icon_base64 = base64.b64encode(image_bytes).decode("ascii")
        except Exception as exc:
            print(f"⚠️ Server-Icon konnte nicht gesichert werden ({type(exc).__name__}).")
    filename = f"{prefix}backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S_%f')}.json"
    # D-03: discord.py caches are not thread-safe – copy the structure in the loop, files in a thread.
    snapshot = build_discord_structure(guild, icon_base64)
    await asyncio.to_thread(add_panel_data, snapshot)
    path = os.path.join(BACKUP_DIR, filename)
    await asyncio.to_thread(save_json, path, snapshot)
    for warning in snapshot.get("snapshot_warnings", []):
        print(f"⚠️ Backup-Hinweis: {warning}")
    if prefix == "auto_":
        await asyncio.to_thread(prune_auto_backups)
    return filename


def _backup_channel_type(channel_data: dict) -> str:
    return str(channel_data.get("type") or "text").lower()


def _make_permission_overwrites(guild, entries, role_map, backup_guild_id: int, warnings: list[str]):
    overwrites = {}
    if not isinstance(entries, list):
        return overwrites
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            target_id = int(entry.get("id"))
            if entry.get("type") == "role":
                target = role_map.get(target_id)
                if target is None and target_id == backup_guild_id:
                    target = guild.default_role
                if target is None:
                    target = guild.get_role(target_id)
            elif entry.get("type") == "member":
                target = guild.get_member(target_id)
            else:
                continue
            if target is None:
                warnings.append(f"Überschreibung für nicht vorhandene Rolle/Mitglied {target_id} übersprungen.")
                continue
            allow = discord.Permissions(int(entry.get("allow", 0)))
            deny = discord.Permissions(int(entry.get("deny", 0)))
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
        except (TypeError, ValueError):
            warnings.append("Ungültige Kanalberechtigung im Backup übersprungen.")
        except Exception as exc:
            warnings.append(f"Kanalberechtigung konnte nicht gelesen werden ({type(exc).__name__}).")
    return overwrites


async def _create_restored_channel(guild, source: dict, category, overwrites, warnings: list[str]):
    name = str(source.get("name") or "restored-channel")[:100]
    kind = _backup_channel_type(source)
    position = max(0, int(source.get("position", 0) or 0))
    reason = "Pulse Server-Backup wiederherstellen"

    if kind == "category":
        return await guild.create_category(
            name, overwrites=overwrites, position=position, reason=reason
        )

    if kind in ("voice", "stage_voice"):
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason,
        }
        if source.get("bitrate") is not None:
            kwargs["bitrate"] = max(8000, min(384000, int(source["bitrate"])))
        if source.get("user_limit") is not None:
            kwargs["user_limit"] = max(0, min(99, int(source["user_limit"])))
        if source.get("rtc_region") is not None:
            kwargs["rtc_region"] = str(source["rtc_region"])
        if source.get("nsfw") is not None:
            kwargs["nsfw"] = bool(source["nsfw"])
        if kind == "stage_voice" and hasattr(guild, "create_stage_channel"):
            # Stage APIs need a topic and don't accept the ordinary voice bitrate/user limit.
            stage_kwargs = {
                "topic": str(source.get("topic") or "Stage-Kanal wiederhergestellt")[:120],
                "category": category,
                "overwrites": overwrites,
                "position": position,
                "reason": reason,
            }
            try:
                return await guild.create_stage_channel(name, **stage_kwargs)
            except TypeError:
                # Compatibility fallback for older supported discord.py releases.
                warnings.append(f"Stage-Kanal {name}: API-Signatur nicht kompatibel, als Sprachkanal erstellt.")
        if kind == "stage_voice":
            warnings.append(f"Stage-Kanal {name}: API nicht verfügbar, als Sprachkanal erstellt.")
        return await guild.create_voice_channel(name, **kwargs)

    if kind in ("forum", "media") and hasattr(guild, "create_forum"):
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason, "nsfw": bool(source.get("nsfw", False)),
        }
        if source.get("topic") is not None:
            kwargs["topic"] = str(source["topic"])[:1024]
        if source.get("slowmode") is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, int(source["slowmode"])))
        if source.get("default_auto_archive_duration") is not None:
            kwargs["default_auto_archive_duration"] = int(source["default_auto_archive_duration"])
        if source.get("default_thread_slowmode_delay") is not None:
            kwargs["default_thread_slowmode_delay"] = int(source["default_thread_slowmode_delay"])
        tags = []
        for tag in source.get("available_tags", [])[:20]:
            if not isinstance(tag, dict) or not tag.get("name"):
                continue
            emoji = None
            if tag.get("emoji_id"):
                try:
                    emoji = discord.PartialEmoji(name=tag.get("emoji_name") or "emoji", id=int(tag["emoji_id"]))
                except (TypeError, ValueError):
                    emoji = None
            elif tag.get("emoji_name"):
                emoji = str(tag["emoji_name"])
            try:
                tags.append(discord.ForumTag(name=str(tag["name"])[:20], moderated=bool(tag.get("moderated")), emoji=emoji))
            except (TypeError, ValueError):
                warnings.append(f"Forum-Tag in {name} wurde übersprungen.")
        if tags:
            kwargs["available_tags"] = tags
        if kind == "media":
            kwargs["media"] = True
        try:
            return await guild.create_forum(name, **kwargs)
        except TypeError:
            # Older discord.py builds may lack the media flag or advanced forum fields.
            kwargs.pop("media", None)
            kwargs.pop("available_tags", None)
            return await guild.create_forum(name, **kwargs)

    if kind == "news":
        creator = getattr(guild, "create_news_channel", None)
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason, "nsfw": bool(source.get("nsfw", False)),
        }
        if source.get("topic") is not None:
            kwargs["topic"] = str(source["topic"])[:1024]
        if source.get("slowmode") is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, int(source["slowmode"])))
        if creator is not None:
            return await creator(name, **kwargs)
        warnings.append(f"Ankündigungskanal {name}: API nicht verfügbar, als Textkanal erstellt.")
        kind = "text"

    if kind in ("text", "news", "store"):
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason, "nsfw": bool(source.get("nsfw", False)),
        }
        if source.get("topic") is not None:
            kwargs["topic"] = str(source["topic"])[:1024]
        if source.get("slowmode") is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, int(source["slowmode"])))
        if source.get("default_auto_archive_duration") is not None:
            kwargs["default_auto_archive_duration"] = int(source["default_auto_archive_duration"])
        if source.get("default_thread_slowmode_delay") is not None:
            kwargs["default_thread_slowmode_delay"] = int(source["default_thread_slowmode_delay"])
        return await guild.create_text_channel(name, **kwargs)

    warnings.append(f"Kanaltyp {kind} ({name}) wird von diesem Restore nicht automatisch angelegt.")
    return None


async def _edit_restored_channel(channel, source: dict, category, overwrites):
    """Apply only real differences, avoiding hundreds of no-op PATCH requests."""
    kwargs = {}
    desired_name = str(source.get("name") or channel.name)[:100]
    desired_position = max(0, int(source.get("position", 0) or 0))
    if channel.name != desired_name:
        kwargs["name"] = desired_name
    if int(getattr(channel, "position", 0) or 0) != desired_position:
        kwargs["position"] = desired_position
    if dict(getattr(channel, "overwrites", {}) or {}) != overwrites:
        kwargs["overwrites"] = overwrites

    if isinstance(channel, discord.CategoryChannel):
        if kwargs:
            kwargs["reason"] = "Pulse Server-Backup wiederherstellen"
            await channel.edit(**kwargs)
        return channel

    desired_category_id = category.id if category else None
    if getattr(channel, "category_id", None) != desired_category_id:
        kwargs["category"] = category
    if hasattr(channel, "topic") and getattr(channel, "topic", None) != source.get("topic"):
        kwargs["topic"] = source.get("topic")
    if hasattr(channel, "nsfw") and bool(getattr(channel, "nsfw", False)) != bool(source.get("nsfw", False)):
        kwargs["nsfw"] = bool(source.get("nsfw", False))
    if hasattr(channel, "slowmode_delay") and source.get("slowmode") is not None:
        desired = max(0, min(21600, int(source["slowmode"])))
        if int(getattr(channel, "slowmode_delay", 0) or 0) != desired:
            kwargs["slowmode_delay"] = desired
    if hasattr(channel, "bitrate") and source.get("bitrate") is not None:
        desired = max(8000, min(384000, int(source["bitrate"])))
        if int(getattr(channel, "bitrate", 0) or 0) != desired:
            kwargs["bitrate"] = desired
    if hasattr(channel, "user_limit") and source.get("user_limit") is not None:
        desired = max(0, min(99, int(source["user_limit"])))
        if int(getattr(channel, "user_limit", 0) or 0) != desired:
            kwargs["user_limit"] = desired
    if hasattr(channel, "rtc_region") and getattr(channel, "rtc_region", None) != source.get("rtc_region"):
        kwargs["rtc_region"] = source.get("rtc_region")
    if hasattr(channel, "default_auto_archive_duration") and source.get("default_auto_archive_duration") is not None:
        desired = int(source["default_auto_archive_duration"])
        if int(getattr(channel, "default_auto_archive_duration", 0) or 0) != desired:
            kwargs["default_auto_archive_duration"] = desired
    if hasattr(channel, "default_thread_slowmode_delay") and source.get("default_thread_slowmode_delay") is not None:
        desired = int(source["default_thread_slowmode_delay"])
        if int(getattr(channel, "default_thread_slowmode_delay", 0) or 0) != desired:
            kwargs["default_thread_slowmode_delay"] = desired

    if hasattr(channel, "available_tags") and isinstance(source.get("available_tags"), list):
        desired_tags = []
        for tag in source.get("available_tags", [])[:20]:
            if not isinstance(tag, dict) or not tag.get("name"):
                continue
            emoji = None
            if tag.get("emoji_id"):
                try:
                    emoji = discord.PartialEmoji(name=tag.get("emoji_name") or "emoji", id=int(tag["emoji_id"]))
                except (TypeError, ValueError):
                    emoji = None
            elif tag.get("emoji_name"):
                emoji = str(tag["emoji_name"])
            try:
                desired_tags.append(discord.ForumTag(name=str(tag["name"])[:20], moderated=bool(tag.get("moderated")), emoji=emoji))
            except (TypeError, ValueError):
                continue
        current_tags = list(getattr(channel, "available_tags", []) or [])
        current_signature = [(tag.name, bool(tag.moderated), getattr(tag.emoji, "name", None), getattr(tag.emoji, "id", None)) for tag in current_tags]
        desired_signature = [(tag.name, bool(tag.moderated), getattr(tag.emoji, "name", None), getattr(tag.emoji, "id", None)) for tag in desired_tags]
        if current_signature != desired_signature:
            kwargs["available_tags"] = desired_tags

    if kwargs:
        kwargs["reason"] = "Pulse Server-Backup wiederherstellen"
        await channel.edit(**kwargs)
    return channel

async def restore_guild_structure(guild, backup: dict, exact_restore: bool = False) -> dict:
    """Restore roles, categories/channels, overwrites, server settings and known member assignments."""
    validate_backup_document(backup, guild.id)
    backup_guild_id = int(backup["guild_id"])
    warnings: list[str] = []
    role_map = {backup_guild_id: guild.default_role}
    channel_map = {}
    restored_object_ids = set()
    restored_roles = 0
    restored_channels = 0
    restored_members = 0
    skipped_members = 0

    current_roles_by_id = {int(role.id): role for role in guild.roles}
    current_roles_by_name = {role.name: role for role in guild.roles}
    role_records = backup.get("roles", [])
    for source in role_records:
        old_id = int(source.get("id", 0) or 0)
        name = str(source.get("name") or "restored-role")[:100]
        role = current_roles_by_id.get(old_id) or current_roles_by_name.get(name)
        is_managed = bool(source.get("managed"))
        if role is None and is_managed:
            warnings.append(f"Verwaltete Discord-Rolle {name} kann nicht neu erstellt werden.")
            continue
        try:
            if role is None:
                role = await guild.create_role(
                    name=name,
                    colour=discord.Colour(int(source.get("color", 0) or 0)),
                    permissions=discord.Permissions(int(source.get("permissions", 0) or 0)),
                    hoist=bool(source.get("hoist", False)),
                    mentionable=bool(source.get("mentionable", False)),
                    reason="Pulse Server-Backup wiederherstellen",
                )
                restored_roles += 1
            elif not role.managed:
                desired = {
                    "name": name,
                    "colour": discord.Colour(int(source.get("color", 0) or 0)),
                    "permissions": discord.Permissions(int(source.get("permissions", 0) or 0)),
                    "hoist": bool(source.get("hoist", False)),
                    "mentionable": bool(source.get("mentionable", False)),
                }
                if (
                    role.name != desired["name"]
                    or role.color.value != desired["colour"].value
                    or role.permissions.value != desired["permissions"].value
                    or role.hoist != desired["hoist"]
                    or role.mentionable != desired["mentionable"]
                ):
                    await role.edit(reason="Pulse Server-Backup wiederherstellen", **desired)
                    restored_roles += 1
            role_map[old_id] = role
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            if role is not None:
                role_map[old_id] = role
            warnings.append(f"Rolle {name} konnte nicht wiederhergestellt werden ({type(exc).__name__}).")

    try:
        positions = {}
        position_changed = False
        for source in role_records:
            role = role_map.get(int(source.get("id", 0) or 0))
            if role is not None and not role.managed and not role.is_default():
                desired_position = max(1, int(source.get("position", 1) or 1))
                positions[role] = desired_position
                if int(role.position) != desired_position:
                    position_changed = True
        if positions and position_changed:
            await guild.edit_role_positions(positions=positions, reason="Pulse Server-Backup wiederherstellen")
    except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
        warnings.append(f"Rollenreihenfolge konnte nicht vollständig wiederhergestellt werden ({type(exc).__name__}).")

    category_records = backup.get("categories", [])
    current_channels_by_id = {int(channel.id): channel for channel in guild.channels}
    current_categories_by_name = {channel.name: channel for channel in guild.categories}
    for source in category_records:
        old_id = int(source.get("id", 0) or 0)
        name = str(source.get("name") or "restored-category")[:100]
        category = current_channels_by_id.get(old_id)
        if not isinstance(category, discord.CategoryChannel):
            possible = current_categories_by_name.get(name)
            category = possible if isinstance(possible, discord.CategoryChannel) else None
        overwrites = _make_permission_overwrites(guild, source.get("overwrites", []), role_map, backup_guild_id, warnings)
        if category is not None:
            restored_object_ids.add(id(category))
            if old_id:
                channel_map[old_id] = category
        try:
            if category is None:
                category = await guild.create_category(
                    name, overwrites=overwrites,
                    position=max(0, int(source.get("position", 0) or 0)),
                    reason="Pulse Server-Backup wiederherstellen",
                )
                restored_channels += 1
            else:
                await _edit_restored_channel(category, source, None, overwrites)
            restored_object_ids.add(id(category))
            if old_id:
                channel_map[old_id] = category
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            if category is not None:
                restored_object_ids.add(id(category))
                if old_id:
                    channel_map[old_id] = category
            warnings.append(f"Kategorie {name} konnte nicht wiederhergestellt werden ({type(exc).__name__}).")

    all_channel_records = []
    for category in category_records:
        parent_id = int(category.get("id", 0) or 0)
        for child in category.get("channels", []):
            if isinstance(child, dict):
                child_copy = dict(child)
                if not child_copy.get("category_id") and parent_id:
                    child_copy["category_id"] = parent_id
                child_copy["category_name"] = str(category.get("name") or "")
                all_channel_records.append(child_copy)
    all_channel_records.extend(backup.get("uncategorized_channels", []))

    for source in all_channel_records:
        if not isinstance(source, dict):
            continue
        old_id = int(source.get("id", 0) or 0)
        name = str(source.get("name") or "restored-channel")[:100]
        kind = _backup_channel_type(source)
        parent_id = int(source.get("category_id", 0) or 0)
        category = channel_map.get(parent_id)
        if category is None and parent_id:
            candidate = guild.get_channel(parent_id)
            category = candidate if isinstance(candidate, discord.CategoryChannel) else None
        if category is None and source.get("category_name"):
            category = next(
                (item for item in guild.categories if item.name == str(source["category_name"])),
                None,
            )
        overwrites = _make_permission_overwrites(guild, source.get("overwrites", []), role_map, backup_guild_id, warnings)

        channel = guild.get_channel(old_id) if old_id else None
        expected_type = kind
        if isinstance(channel, discord.CategoryChannel) or channel is not None and str(channel.type) != expected_type:
            channel = None
        if channel is None:
            channel = next(
                (
                    item for item in guild.channels
                    if not isinstance(item, discord.CategoryChannel)
                    and item.name == name
                    and str(item.type) == expected_type
                    and (getattr(item, "category_id", None) or 0) == (category.id if category else 0)
                ),
                None,
            )
        if channel is not None:
            restored_object_ids.add(id(channel))
            if old_id:
                channel_map[old_id] = channel
        try:
            if channel is None:
                channel = await _create_restored_channel(guild, source, category, overwrites, warnings)
                if channel is not None:
                    restored_channels += 1
            else:
                await _edit_restored_channel(channel, source, category, overwrites)
                restored_channels += 1
            if channel is not None:
                restored_object_ids.add(id(channel))
                if old_id:
                    channel_map[old_id] = channel
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            if channel is not None:
                restored_object_ids.add(id(channel))
                if old_id:
                    channel_map[old_id] = channel
            warnings.append(f"Kanal {name} konnte nicht wiederhergestellt werden ({type(exc).__name__}).")

    # Restore server properties only after the channels they refer to exist.
    settings = backup.get("guild_settings", {}) if isinstance(backup.get("guild_settings"), dict) else {}
    settings_kwargs = {}
    if settings.get("name") and guild.name != str(settings["name"])[:100]:
        settings_kwargs["name"] = str(settings["name"])[:100]
    if "description" in settings and settings.get("description") != getattr(guild, "description", None):
        settings_kwargs["description"] = settings.get("description")
    enum_fields = (
        ("verification_level", "verification_level", discord.VerificationLevel),
        ("default_notifications", "default_notifications", discord.NotificationLevel),
        ("explicit_content_filter", "explicit_content_filter", discord.ContentFilter),
    )
    for source_key, target_key, enum_type in enum_fields:
        value = settings.get(source_key)
        if value is not None:
            try:
                current_value = _enum_int(getattr(guild, target_key, None))
                if current_value != int(value):
                    settings_kwargs[target_key] = enum_type(int(value))
            except (TypeError, ValueError):
                warnings.append(f"Servereinstellung {source_key} war ungültig und wurde übersprungen.")
    if settings.get("afk_timeout") is not None:
        try:
            if _enum_int(getattr(guild, "afk_timeout", None)) != int(settings["afk_timeout"]):
                settings_kwargs["afk_timeout"] = int(settings["afk_timeout"])
        except (TypeError, ValueError):
            pass
    if settings.get("premium_progress_bar_enabled") is not None:
        if bool(getattr(guild, "premium_progress_bar_enabled", False)) != bool(settings["premium_progress_bar_enabled"]):
            settings_kwargs["premium_progress_bar_enabled"] = bool(settings["premium_progress_bar_enabled"])
    if settings.get("preferred_locale"):
        try:
            restored_locale = discord.Locale(str(settings["preferred_locale"]))
            if str(getattr(guild, "preferred_locale", "")) != str(restored_locale):
                settings_kwargs["preferred_locale"] = restored_locale
        except (TypeError, ValueError):
            warnings.append("Server-Sprache wurde nicht wiederhergestellt (unbekannte Discord-Locale).")

    for key, source_key in (
        ("system_channel", "system_channel_id"),
        ("rules_channel", "rules_channel_id"),
        ("public_updates_channel", "public_updates_channel_id"),
        ("afk_channel", "afk_channel_id"),
    ):
        if source_key not in settings:
            continue
        old_channel_id = settings.get(source_key)
        if old_channel_id is None:
            if getattr(guild, key, None) is not None:
                settings_kwargs[key] = None
            continue
        mapped = channel_map.get(int(old_channel_id))
        if mapped is None:
            mapped = guild.get_channel(int(old_channel_id))
        if mapped is not None and getattr(guild, key, None) != mapped:
            settings_kwargs[key] = mapped

    if settings_kwargs:
        try:
            await guild.edit(reason="Pulse Server-Backup wiederherstellen", **settings_kwargs)
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
            warnings.append(f"Servereinstellungen konnten nicht vollständig gesetzt werden ({type(exc).__name__}).")
            for key, value in settings_kwargs.items():
                try:
                    await guild.edit(reason="Pulse Server-Backup wiederherstellen", **{key: value})
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    warnings.append(f"Servereinstellung {key} konnte nicht wiederhergestellt werden.")

    icon_b64 = settings.get("icon_base64")
    stored_icon_key = settings.get("icon_key")
    current_icon_key = getattr(getattr(guild, "icon", None), "key", None)
    if isinstance(icon_b64, str) and icon_b64 and stored_icon_key != current_icon_key:
        try:
            icon_bytes = base64.b64decode(icon_b64, validate=True)
            if 0 < len(icon_bytes) <= 8 * 1024 * 1024:
                await guild.edit(icon=icon_bytes, reason="Pulse Server-Backup wiederherstellen")
            else:
                warnings.append("Server-Icon im Backup überschreitet das erlaubte Größenlimit.")
        except (ValueError, TypeError, discord.Forbidden, discord.HTTPException) as exc:
            warnings.append(f"Server-Icon konnte nicht wiederhergestellt werden ({type(exc).__name__}).")
    elif "icon_base64" in settings and "icon_key" in settings and not icon_b64 and stored_icon_key is None and current_icon_key is not None:
        try:
            await guild.edit(icon=None, reason="Pulse Server-Backup wiederherstellen")
        except (TypeError, discord.Forbidden, discord.HTTPException) as exc:
            warnings.append(f"Server-Icon konnte nicht zurückgesetzt werden ({type(exc).__name__}).")

    mapped_channels = set(restored_object_ids)
    mapped_channels.update(id(channel) for channel in channel_map.values())
    mapped_roles = {id(role) for role in role_map.values()}
    if exact_restore:
        bot_member = guild.me
        bot_top = bot_member.top_role.position if bot_member else 0
        extra_channels = [
            channel for channel in guild.channels
            if id(channel) not in mapped_channels
        ]
        extra_channels.sort(key=lambda item: isinstance(item, discord.CategoryChannel))
        for channel in extra_channels:
            try:
                await channel.delete(reason="Pulse exakte Server-Backup-Wiederherstellung")
            except (discord.Forbidden, discord.HTTPException):
                warnings.append(f"Zusätzlicher Kanal {channel.name} konnte nicht entfernt werden.")
        for role in list(guild.roles):
            if role.is_default() or role.managed or id(role) in mapped_roles:
                continue
            if bot_member and role.position >= bot_top:
                warnings.append(f"Zusätzliche Rolle {role.name} bleibt wegen der Bot-Rollenhierarchie erhalten.")
                continue
            try:
                await role.delete(reason="Pulse exakte Server-Backup-Wiederherstellung")
            except (discord.Forbidden, discord.HTTPException):
                warnings.append(f"Zusätzliche Rolle {role.name} konnte nicht entfernt werden.")

    member_records = backup.get("member_assignments", [])
    if isinstance(member_records, list):
        snapshot_role_objects = {role.id for key, role in role_map.items() if key != backup_guild_id}
        for source in member_records:
            try:
                member_id = int(source.get("id"))
                member = guild.get_member(member_id)
                if member is None:
                    skipped_members += 1
                    continue
                desired_roles = []
                for old_role_id in source.get("role_ids", []):
                    role = role_map.get(int(old_role_id))
                    if role is not None and not role.is_default() and not role.managed and role not in desired_roles:
                        desired_roles.append(role)
                for role in member.roles:
                    if role.is_default() or role in desired_roles:
                        continue
                    # Safe mode is additive for member roles; exact mode may reset
                    # assignable role assignments, but integration-managed roles remain.
                    if role.managed or not exact_restore:
                        desired_roles.append(role)
                current_role_ids = {role.id for role in member.roles if not role.is_default()}
                desired_role_ids = {role.id for role in desired_roles}
                desired_nick = source.get("nick")
                if current_role_ids == desired_role_ids and member.nick == desired_nick:
                    continue
                await member.edit(
                    roles=desired_roles,
                    nick=desired_nick,
                    reason="Pulse Server-Backup wiederherstellen",
                )
                restored_members += 1
            except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
                warnings.append(f"Mitgliedsrollen/-name konnten nicht wiederhergestellt werden ({type(exc).__name__}).")

    id_remap = {}
    for old_id, role in role_map.items():
        if role is not None:
            id_remap[str(int(old_id))] = int(role.id)
    for old_id, channel in channel_map.items():
        if channel is not None:
            id_remap[str(int(old_id))] = int(channel.id)

    return {
        "roles_changed": restored_roles,
        "channels_changed": restored_channels,
        "members_changed": restored_members,
        "members_skipped": skipped_members,
        "exact_restore": bool(exact_restore),
        "id_remap": id_remap,
        "warnings": warnings[:50],
        "warning_count": len(warnings),
    }


MAX_BACKUP_UPLOAD_BYTES = 50 * 1024 * 1024


async def auto_backup_loop():
    if AUTO_BACKUP_HOURS <= 0:
        return
    import pulse_jobs  # P-06: the auto-backup check is visible in the job registry
    while True:
        await asyncio.sleep(300)
        try:
            async with pulse_jobs.job("Auto-Backup-Prüfung (5 min)", 300):
                bot = getattr(app.state, "bot", None)
                guild = bot.get_guild(GUILD_ID) if bot else None
                if guild:
                    times = [
                        os.path.getmtime(os.path.join(BACKUP_DIR, f))
                        for f in os.listdir(BACKUP_DIR)
                        if BACKUP_NAME_RE.fullmatch(f)
                    ]
                    if not times or time.time() - max(times) > AUTO_BACKUP_HOURS * 3600:
                        name = await create_backup_file_async(guild, "auto_")
                        log_audit("System", "0", "Auto-Backup", name)
                        await run_auto_full_backup(guild)  # D-01/D-04: complete ZIP, retention, weekly restore test

                        # Optional off-host copy. The local backup remains valid even
                        # when Discord uploads fail or the attachment exceeds the cap.
                        raw_channel_id = os.getenv("PULSE_BACKUP_DISCORD_CHANNEL_ID", "").strip()
                        if not raw_channel_id:
                            raw_channel_id = str(load_config().get("backup_discord_channel_id", "") or "")
                        try:
                            channel_id = int(raw_channel_id)
                        except (TypeError, ValueError):
                            channel_id = 0
                        channel = (bot.get_channel(channel_id) or guild.get_channel(channel_id)) if channel_id else None
                        if channel and hasattr(channel, "send"):
                            full_path = os.path.join(BACKUP_DIR, name)
                            size = os.path.getsize(full_path)
                            embed = discord.Embed(
                                title="💾 Automatische Pulse-Sicherung",
                                description=(
                                    "Eine vollständige Pulse-Panel-Sicherung wurde erstellt. "
                                    "Die lokale Kopie bleibt zusätzlich im Dashboard unter /backups verfügbar."
                                ),
                                color=_pulse_color("success") if size <= 7_500_000 else _pulse_color("warning"),
                                timestamp=now_de(),
                            )
                            embed.add_field(name="Datei", value=os.path.basename(name), inline=False)
                            embed.add_field(name="Größe", value=f"{size / (1024 * 1024):.2f} MiB", inline=True)
                            try:
                                if size <= 7_500_000:
                                    await send_styled(
                                        channel,
                                        embed=embed,
                                        file=discord.File(full_path, filename=os.path.basename(name)),
                                        allowed_mentions=discord.AllowedMentions.none(),
                                    )
                                else:
                                    embed.add_field(
                                        name="Offsite-Kopie",
                                        value="Upload übersprungen: Datei ist zu groß. Nur die lokale Sicherung wurde erstellt.",
                                        inline=False,
                                    )
                                    await send_styled(
                                        channel,
                                        embed=embed,
                                        allowed_mentions=discord.AllowedMentions.none(),
                                    )
                                log_audit("System", "0", "Auto-Backup Discord-Kopie", f"{name} · {size} Bytes")
                            except (discord.Forbidden, discord.HTTPException, OSError) as upload_error:
                                log_audit(
                                    "System", "0", "Auto-Backup Upload Fehler",
                                    f"{name} · {type(upload_error).__name__}",
                                )
                                print(f"⚠️ Auto-Backup ist lokal vorhanden; Discord-Kopie fehlgeschlagen ({type(upload_error).__name__}).")
        except Exception as e:
            print(f"Auto-Backup fehlgeschlagen: {type(e).__name__}: {e}")
            _system_alert("backup:auto", f"🔴 **Auto-Backup fehlgeschlagen:** `{type(e).__name__}` – vorhandene Sicherungen bleiben unverändert.")


async def _start_background_tasks():
    # Initialise one small shared HTTP pool and keep the backup worker singular.
    _roblox_http_client()
    task = getattr(app.state, "backup_task", None)
    if task is None or task.done():
        app.state.backup_task = spawn_task(auto_backup_loop(), name="pulse-auto-backup", critical=True)


async def _stop_background_tasks():
    task = getattr(app.state, "backup_task", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    client = getattr(app.state, "roblox_http_client", None)
    if client is not None and not client.is_closed:
        await client.aclose()
    try:  # Server Control Center: shared HTTP pool to the host agent / GitHub
        import pulse_server_control
        await pulse_server_control.aclose()
    except Exception as exc:
        print(f"⚠️ Server Control Center: HTTP-Pool nicht geschlossen ({type(exc).__name__})")


@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    files = sorted(
        (name for name in os.listdir(BACKUP_DIR) if BACKUP_NAME_RE.fullmatch(name)),
        key=lambda name: os.path.getmtime(os.path.join(BACKUP_DIR, name)),
        reverse=True,
    )
    rows = ""
    for name in files:
        path = os.path.join(BACKUP_DIR, name)
        created = datetime.fromtimestamp(os.path.getmtime(path), TZ).strftime("%d.%m.%Y %H:%M") if TZ else \
            datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
        size = os.path.getsize(path)
        auto = ' <span class="text-[10px] px-2 py-0.5 rounded-full border border-indigo-500/30 bg-indigo-500/10 text-indigo-500 font-sans">AUTO</span>' if name.startswith("auto_") else ""
        uploaded = ' <span class="text-[10px] px-2 py-0.5 rounded-full border border-cyan-500/30 bg-cyan-500/10 text-cyan-500 font-sans">IMPORT</span>' if name.startswith("uploaded_") else ""
        rows += f"""
        <div class="{CARD} p-4 flex flex-col gap-3">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                    <div class="font-bold text-slate-900 dark:text-white text-sm font-mono break-all">{esc(name)}{auto}{uploaded}</div>
                    <div class="text-xs text-slate-400 mt-0.5">{created} · {round(size / 1024, 1)} KB</div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <a href="/backup/download/{esc(name)}" class="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs px-3.5 py-2 rounded-xl text-slate-700 dark:text-slate-300 font-medium transition shadow-sm">📥 JSON herunterladen</a>
                    <form action="/backup/delete" method="post" onsubmit="return confirm('Backup wirklich endgültig löschen?');">
                        <input type="hidden" name="filename" value="{esc(name)}">
                        <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">🗑️ Löschen</button>
                    </form>
                </div>
            </div>
            <form action="/backup/restore" method="post" class="border-t border-slate-200 dark:border-slate-700 pt-3 flex flex-col gap-2" onsubmit="return confirm('Vollständige Server- und Pulse-Wiederherstellung starten? Es wird vorher automatisch eine Sicherheitskopie des aktuellen Zustands angelegt.');">
                <input type="hidden" name="filename" value="{esc(name)}">
                <label class="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300">
                    <input type="checkbox" name="exact_restore" value="true" class="mt-0.5 rounded">
                    <span><strong>Exakte Struktur erzwingen:</strong> löscht zusätzliche, nicht im Backup enthaltene Kanäle und nicht verwaltete Rollen (nur soweit die Bot-Hierarchie es erlaubt).</span>
                </label>
                <button class="self-start bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">↩️ Server + Pulse wiederherstellen</button>
            </form>
        </div>"""

    auto_text = (
        f"Automatische Sicherungen alle {AUTO_BACKUP_HOURS:g} Stunden; bis zu {MAX_BACKUPS} automatische Dateien bleiben erhalten."
        if AUTO_BACKUP_HOURS > 0 else "Automatische Sicherung ist deaktiviert (AUTO_BACKUP_HOURS=0)."
    )
    body = f"""
    <div class="flex flex-col gap-5 mb-6 max-w-4xl">
        <div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Discord Server Backups</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Sichert Servername/-einstellungen, Rollen samt Berechtigungen und Reihenfolge, Kategorien, Kanäle samt Rechten, vorhandene Mitgliedsrollen/-Spitznamen sowie Pulse-Datenbanken, Konfiguration, Warnhistorien und Transkripte. {auto_text}</p>
        </div>
        <div class="{CARD} p-4 flex flex-col gap-3">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">💾 Neues vollständiges Backup</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400">Erstellt eine herunterladbare JSON-Datei. Für jede Wiederherstellung legt Pulse vorher automatisch eine Rückfall-Sicherung an.</p>
            <form action="/backup/create" method="post">
                <button class="{BTN} text-xs py-3 px-5">💾 Server + Pulse sichern</button>
            </form>
        </div>
        {full_backup_section()}
        <div class="{CARD} p-4 flex flex-col gap-3">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">📤 Backup hochladen</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400">Importiert ein zuvor von Pulse heruntergeladenes JSON-Backup (max. {MAX_BACKUP_UPLOAD_BYTES // (1024 * 1024)} MiB). Das Backup muss zu diesem Discord-Server gehören und wird vor dem Speichern geprüft.</p>
            <form action="/backup/upload" method="post" enctype="multipart/form-data" class="flex flex-col sm:flex-row gap-3">
                <input type="file" name="backup_file" accept=".json,application/json" required class="block w-full text-xs text-slate-600 dark:text-slate-300">
                <button class="{BTN} text-xs py-3 px-5 shrink-0">⬆️ Backup importieren</button>
            </form>
        </div>
        <div class="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-slate-600 dark:text-slate-300">
            <strong>Grenzen von Discord:</strong> Bot-Backups können keine alten Nachrichten/Verläufe, privaten DMs, externe Integrationen, Webhooks, Einladungen, Server-Boosts oder nicht mehr verfügbare benutzerdefinierte Emojis rekonstruieren. Mitglieder werden weder eingeladen noch entfernt. Für das Wiederherstellen von Rollen/Kanälen braucht Pulse passende Discord-Rechte und eine ausreichend hohe Bot-Rolle.
        </div>
    </div>
    <div class="space-y-3 max-w-4xl">{rows or f"<div class='text-xs text-slate-400 italic {CARD} p-6 text-center'>Noch keine Backups vorhanden.</div>"}</div>"""
    return render_page("Server Backups", ctx, "backups", body)


@app.post("/backup/create")
async def create_backup(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    try:
        name = await create_backup_file_async(ctx.guild)
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Server-Backup Erstellt", f"Filename: {name}")
        return back("/backups", f"Server- und Pulse-Backup erstellt: {name}")
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Server-Backup Fehlgeschlagen", type(exc).__name__)
        return back("/backups", f"Backup fehlgeschlagen ({type(exc).__name__}). Vorhandene Sicherungen wurden nicht verändert.", False)


@app.post("/backup/upload")
async def upload_backup(
    request: Request,
    backup_file: UploadFile = File(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm=None, admin=True)
    try:
        if not backup_file.filename or not backup_file.filename.lower().endswith(".json"):
            return back("/backups", "Bitte eine Pulse-Backup-Datei mit der Endung .json auswählen.", False)
        raw = await backup_file.read(MAX_BACKUP_UPLOAD_BYTES + 1)
        if len(raw) > MAX_BACKUP_UPLOAD_BYTES:
            return back("/backups", f"Datei zu groß. Maximum: {MAX_BACKUP_UPLOAD_BYTES // (1024 * 1024)} MiB.", False)
        try:
            backup = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Die Datei ist keine gültige UTF-8-JSON-Datei.") from exc
        validate_backup_document(backup, ctx.guild.id)
        settings = backup.get("guild_settings", {})
        if isinstance(settings, dict) and isinstance(settings.get("icon_base64"), str):
            icon_raw = base64.b64decode(settings["icon_base64"], validate=True)
            if len(icon_raw) > 8 * 1024 * 1024:
                raise ValueError("Das Server-Icon im Backup ist zu groß.")
        sqlite_files = backup.get("panel_data", {}).get("sqlite_files_base64", {})
        for database_name, encoded in sqlite_files.items():
            if database_name != "activity_check.db" or not isinstance(encoded, str):
                raise ValueError("Unerlaubte Datenbankdatei im Backup.")
            if len(encoded) > 28 * 1024 * 1024:
                raise ValueError("Die Datenbankdatei im Backup ist zu groß.")
            decoded = base64.b64decode(encoded, validate=True)
            if len(decoded) > 20 * 1024 * 1024 or not decoded.startswith(b"SQLite format 3\x00"):
                raise ValueError("Die SQLite-Datei im Backup ist beschädigt oder ungültig.")
        name = f"uploaded_backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S_%f')}.json"
        safe_path = os.path.join(BACKUP_DIR, name)
        # Normalize accepted JSON and write atomically; never trust the user-supplied filename.
        temp_path = safe_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(backup, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_path, safe_path)
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Importiert", f"Filename: {name}")
        return back("/backups", f"Backup geprüft und importiert: {name}")
    except (ValueError, OSError) as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Import Abgelehnt", f"{type(exc).__name__}: {str(exc)[:180]}")
        return back("/backups", f"Backup-Import abgelehnt: {str(exc)[:240]}", False)
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Import Fehlgeschlagen", type(exc).__name__)
        return back("/backups", f"Backup konnte nicht importiert werden ({type(exc).__name__}).", False)
    finally:
        await backup_file.close()


@app.get("/backup/download/{filename}")
async def download_backup(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    return FileResponse(path, media_type="application/json", filename=os.path.basename(path))


def _remap_backup_ids(value, id_map: dict[str, int]):
    """Replace only exact Discord snowflake references, preserving all other values."""
    if isinstance(value, dict):
        return {key: _remap_backup_ids(item, id_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_backup_ids(item, id_map) for item in value]
    if isinstance(value, int) and not isinstance(value, bool):
        return id_map.get(str(value), value)
    if isinstance(value, str) and value.isdigit():
        return str(id_map.get(value, int(value)))
    return value


@app.post("/backup/restore")
async def restore_backup(
    request: Request,
    filename: str = Form(...),
    exact_restore: bool = Form(False),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    try:
        if os.path.getsize(path) > MAX_BACKUP_UPLOAD_BYTES:
            raise ValueError("Backup überschreitet das zulässige Dateigrößenlimit.")
        with open(path, "r", encoding="utf-8") as fh:
            backup = json.load(fh)
        validate_backup_document(backup, ctx.guild.id)
        panel = backup["panel_data"]

        # The legacy server-structure archive contains exported JSON only. It
        # cannot restore melonly_logs/melonly_logs_meta. Refuse before any
        # Discord or application state is modified; use the verified Full ZIP.
        ensure_panel_backup_restore_backend_compatible(get_melonly_repository())

        # Current state is snapshotted before touching Discord or Pulse runtime data.
        safety_snapshot = await create_backup_file_async(ctx.guild, "pre_restore_")
        structure_report = await restore_guild_structure(ctx.guild, backup, exact_restore=exact_restore)

        # Re-map IDs if deleted roles/channels had to be recreated with new Discord IDs.
        id_remap = structure_report.pop("id_remap", {})
        if id_remap:
            panel = _remap_backup_ids(panel, id_remap)

        restored_config = panel.get("config")
        if isinstance(restored_config, dict):
            aliases = restored_config.get("backup_role_id_aliases", {})
            if not isinstance(aliases, dict):
                aliases = {}
            built_in_role_ids = (
                list(WARN_ROLE_IDS.values())
                + [WARN_UPRANK_LOCK_ROLE_ID, WARN_IC_LOCK_ROLE_ID, TEST_SUPPORTER_ROLE_ID]
                + list(TEAM_KICK_KEEP_ROLE_IDS)
            )
            for original_id in built_in_role_ids:
                replacement = id_remap.get(str(original_id))
                if replacement is not None and int(replacement) != int(original_id):
                    aliases[str(int(original_id))] = int(replacement)
            restored_config["backup_role_id_aliases"] = aliases

        mapping = {
            "team_data": DATA_FILE, "config": CONFIG_FILE, "logs": LOGS_FILE,
            "shifts": SHIFTS_FILE, "meetings": MEETINGS_FILE, "applications": APPS_FILE,
        }
        for key, target in mapping.items():
            if key in panel:
                save_json(target, panel[key])
        if isinstance(panel.get("pulse_db"), dict):
            pulse_db.import_state(_remap_backup_ids(panel["pulse_db"], id_remap))

        if isinstance(panel.get("loas"), list):
            with closing(sqlite3.connect(DB_ABMELDUNGEN, timeout=5.0)) as conn, conn:
                conn.execute("DELETE FROM abmeldungen")
                conn.executemany(
                    "INSERT OR REPLACE INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)",
                    [
                        (item.get("user_id"), item.get("user_name", ""), item.get("grund", ""),
                         item.get("von", ""), item.get("bis", ""), item.get("original_nick", ""),
                         item.get("guild_id", GUILD_ID))
                        for item in panel["loas"] if isinstance(item, dict) and item.get("user_id") is not None
                    ],
                )

        sqlite_files = panel.get("sqlite_files_base64", {})
        if isinstance(sqlite_files, dict) and "activity_check.db" in sqlite_files:
            decoded = base64.b64decode(sqlite_files["activity_check.db"], validate=True)
            if not decoded.startswith(b"SQLite format 3\x00") or len(decoded) > 20 * 1024 * 1024:
                raise ValueError("Activity-Check-Datenbank im Backup ist beschädigt.")
            db_path = str(DATA_DIR / "activity_check.db")
            temp_db = db_path + ".restore.tmp"
            with open(temp_db, "wb") as fh:
                fh.write(decoded)
            os.replace(temp_db, db_path)

        os.makedirs(os.path.join(DATA_DIR, "transcripts"), exist_ok=True)
        for name, transcript in (panel.get("transcripts") or {}).items():
            safe = os.path.basename(str(name))
            if safe.endswith(".txt") and isinstance(transcript, str) and len(transcript) <= 2 * 1024 * 1024:
                with open(os.path.join(DATA_DIR, "transcripts", safe), "w", encoding="utf-8") as fh:
                    fh.write(transcript)

        warning_text = ""
        if structure_report["warnings"]:
            warning_text = (
                f" Hinweis: {structure_report['warning_count']} Strukturwarnung(en); "
                "Details stehen im Hosting-Log/Audit."
            )
            print("⚠️ Server-Backup mit Strukturwarnungen: " + " | ".join(structure_report["warnings"][:12]))

        details = (
            f"Datei: {os.path.basename(path)} · Sicherheitskopie: {safety_snapshot} · "
            f"Rollenänderungen: {structure_report['roles_changed']} · "
            f"Kanaländerungen: {structure_report['channels_changed']} · "
            f"Mitglieder aktualisiert: {structure_report['members_changed']} · "
            f"Exakte Struktur: {structure_report['exact_restore']} · "
            f"Strukturwarnungen: {structure_report['warning_count']}"
        )
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Server + Pulse wiederhergestellt", details)
        return back(
            "/backups",
            "Wiederherstellung abgeschlossen. "
            f"Rollen: {structure_report['roles_changed']}, Kanäle/Kategorien: {structure_report['channels_changed']}, "
            f"Mitglieder: {structure_report['members_changed']}. Sicherheitskopie: {safety_snapshot}.{warning_text}",
        )
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Wiederherstellung Fehlgeschlagen", f"{os.path.basename(path)} · {type(exc).__name__}: {str(exc)[:180]}")
        print(f"❌ Backup-Wiederherstellung fehlgeschlagen: {type(exc).__name__}: {exc}")
        return back("/backups", f"Wiederherstellung fehlgeschlagen ({type(exc).__name__}). Sicherheitskopie vor Restore wurde, falls begonnen, behalten.", False)


@app.post("/backup/delete")
async def delete_backup(request: Request, filename: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    os.remove(path)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Gelöscht", f"Filename: {os.path.basename(path)}")
    return back("/backups", "Backup gelöscht.")


# =============================================================
# D-01..D-04: Komplett-Backup (ZIP mit Manifest) – alle Datenbanken, JSON-Dateien und Uploads
# =============================================================
FULL_BACKUP_RESTORE_TEST_KEY = "backup_restore_test"
FULL_BACKUP_RESTORE_TEST_DAYS = 7
FULL_BACKUP_MIN_FREE_BYTES = 200 * 1024 * 1024


def full_backup_dir() -> str:
    return os.path.abspath(BACKUP_DIR)


def safe_full_backup_path(filename: str):
    """The existing backup file with exactly this name, or None. The path comes from the directory
    listing, never from the request (no path built from user input – CodeQL py/path-injection)."""
    import pulse_backup
    name = os.path.basename(filename or "")
    if not pulse_backup.NAME_RE.fullmatch(name):
        return None
    for path in pulse_backup.list_backups(full_backup_dir()):
        if path.name == name:
            return str(path)
    return None


async def create_full_backup_async(guild, prefix: str = ""):
    """D-03: Discord structure is copied in the event loop, all file/ZIP work runs in a thread."""
    import pulse_backup
    structure = build_discord_structure(guild) if guild is not None else None
    return await asyncio.to_thread(pulse_backup.create_full_backup, DATA_DIR, full_backup_dir(),
                                   discord_structure=structure, prefix=prefix)


def full_backup_status() -> dict:
    """Latest complete backup and the last automatic restore test (for system status)."""
    import pulse_backup
    backups = pulse_backup.list_backups(full_backup_dir())
    test = pulse_db.get_setting(FULL_BACKUP_RESTORE_TEST_KEY, {}) or {}
    total = sum(p.stat().st_size for p in backups)
    return {"latest": backups[0].name if backups else "", "count": len(backups), "restore_test": test,
            "total_mb": round(total / (1024 * 1024), 1), "total_ok": total <= pulse_backup.TOTAL_MAX_BYTES}


async def run_auto_full_backup(guild) -> None:
    """Called from the existing auto-backup loop (no extra loop): backup, retention, weekly restore test."""
    import pulse_backup
    try:
        os.makedirs(full_backup_dir(), exist_ok=True)
        free = shutil.disk_usage(full_backup_dir()).free
        latest = pulse_backup.list_backups(full_backup_dir())
        needed = FULL_BACKUP_MIN_FREE_BYTES + (2 * latest[0].stat().st_size if latest else 0)
        if free < needed:
            log_audit("System", "0", "Komplett-Backup übersprungen", f"Zu wenig Speicher frei ({free // (1024 * 1024)} MB)")
            print("⚠️ Komplett-Backup übersprungen: zu wenig freier Speicherplatz.")
            return
        await asyncio.to_thread(pulse_backup.prune_auto_backups, full_backup_dir())
        if pulse_backup.total_size(full_backup_dir()) > pulse_backup.TOTAL_MAX_BYTES:
            log_audit("System", "0", "Komplett-Backup übersprungen",
                      f"Gesamtgröße der Sicherungen über {pulse_backup.TOTAL_MAX_BYTES // (1024 * 1024)} MB (PULSE_BACKUP_TOTAL_MAX_MB) – "
                      "bitte alte manuelle Sicherungen löschen")
            print("⚠️ Komplett-Backup übersprungen: Gesamtgrenze der Sicherungen erreicht.")
            return
        path, manifest = await create_full_backup_async(guild, "auto_")
        removed = await asyncio.to_thread(pulse_backup.prune_auto_backups, full_backup_dir())
        details = f"{path.name} · {len(manifest['files'])} Dateien · {len(removed)} alte entfernt"
        if manifest["warnings"]:
            details += " · Hinweise: " + "; ".join(manifest["warnings"])[:300]
            print("⚠️ Komplett-Backup: " + "; ".join(manifest["warnings"]))
        log_audit("System", "0", "Komplett-Backup", details)
        last = parse_timestamp((pulse_db.get_setting(FULL_BACKUP_RESTORE_TEST_KEY, {}) or {}).get("at"))
        if last is None or (datetime.now(last.tzinfo) - last).days >= FULL_BACKUP_RESTORE_TEST_DAYS:
            result = await asyncio.to_thread(pulse_backup.restore_test, path)
            pulse_db.set_setting(FULL_BACKUP_RESTORE_TEST_KEY, result)
            log_audit("System", "0", "Restore-Test " + ("bestanden" if result["ok"] else "FEHLGESCHLAGEN"),
                      f"{result['backup']} · " + ("; ".join(result["problems"])[:300] or "alle Datenbanken intakt"))
            if not result["ok"]:
                print("🔴 Restore-Test fehlgeschlagen: " + "; ".join(result["problems"])[:300])
                _system_alert("backup:restore-test", "🔴 **Restore-Test fehlgeschlagen:** " + "; ".join(result["problems"])[:300])
    except Exception as exc:
        print(f"⚠️ Komplett-Backup fehlgeschlagen: {type(exc).__name__}: {exc}")
        _system_alert("backup:full", f"🔴 **Komplett-Backup fehlgeschlagen:** `{type(exc).__name__}` – vorhandene Sicherungen bleiben unverändert.")


def full_backup_section() -> str:
    import pulse_backup
    rows = ""
    for path in pulse_backup.list_backups(full_backup_dir())[:40]:
        created = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        tag = (" · AUTO" if "_auto_" in path.name else " · SICHERHEITSKOPIE" if "_pre_restore_" in path.name
               else " · VOR DEPLOY" if "_predeploy_" in path.name else " · VOR ROLLBACK" if "_prerollback_" in path.name else "")
        rows += f"""
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-t border-slate-200 dark:border-slate-800 pt-3">
            <div><div class="font-mono text-xs break-all">{esc(path.name)}{tag}</div>
                 <div class="text-[11px] text-slate-400">{created} · {round(path.stat().st_size / (1024 * 1024), 2)} MB</div></div>
            <div class="flex flex-wrap gap-2">
                <a href="/backup/full/download/{esc(path.name)}" class="bg-slate-100 dark:bg-slate-800 text-xs px-3 py-2 rounded-xl">📥 ZIP</a>
                <a href="/backup/full/preview/{esc(path.name)}" class="bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-3 py-2 rounded-xl">🔍 Probelauf / Wiederherstellen</a>
                <form action="/backup/full/delete" method="post" onsubmit="return confirm('Komplett-Backup endgültig löschen?');">
                    <input type="hidden" name="filename" value="{esc(path.name)}">
                    <button class="bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3 py-2 rounded-xl">🗑️</button></form>
            </div>
        </div>"""
    status = full_backup_status()
    test = status["restore_test"]
    test_line = ("Noch kein automatischer Restore-Test." if not test else
                 f"Letzter Restore-Test: {esc(display(test.get('at')))} · {'✅ bestanden' if test.get('ok') else '❌ fehlgeschlagen: ' + esc('; '.join(test.get('problems', []))[:200])}")
    return f"""
        <div class="{CARD} p-4 flex flex-col gap-3">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">🗄️ Komplett-Backup (alle Pulse-Daten)</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400">Ein ZIP mit allen Datenbanken ({len(pulse_backup.SQLITE_FILES)}), allen JSON-Dateien ({len(pulse_backup.JSON_FILES)}), Uploads, Beweisen und Transkripten plus Prüfsummen-Manifest und Discord-Struktur. Nicht enthalten: <code>.env</code> und <code>.session_secret</code>. Automatisch mit jeder Auto-Sicherung; aufbewahrt werden 7 tägliche, 4 wöchentliche und 6 monatliche. {test_line}</p>
            <form action="/backup/full/create" method="post"><button class="{BTN} text-xs py-3 px-5">🗄️ Komplett-Backup jetzt erstellen</button></form>
            {rows or '<div class="text-xs text-slate-400">Noch kein Komplett-Backup vorhanden.</div>'}
        </div>"""


@app.post("/backup/full/create")
async def full_backup_create(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    try:
        path, manifest = await create_full_backup_async(ctx.guild)
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup fehlgeschlagen", type(exc).__name__)
        return back("/backups", f"Komplett-Backup fehlgeschlagen ({type(exc).__name__}). Vorhandene Sicherungen bleiben unverändert.", False)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup erstellt", f"{path.name} · {len(manifest['files'])} Dateien")
    hint = (" Hinweis: " + "; ".join(manifest["warnings"])) if manifest["warnings"] else ""
    return back("/backups", f"Komplett-Backup erstellt: {path.name} ({len(manifest['files'])} Dateien).{hint}", not manifest["warnings"])


@app.get("/backup/full/download/{filename}")
async def full_backup_download(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    return FileResponse(path, media_type="application/zip", filename=os.path.basename(path))


@app.get("/backup/full/preview/{filename}", response_class=HTMLResponse)
async def full_backup_preview(request: Request, filename: str, user_session: str = Cookie(None)):
    """D-01/D-02: dry run – shows what a restore would change. Writes nothing."""
    import pulse_backup
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    try:
        preview = await asyncio.to_thread(pulse_backup.preview_restore, path, DATA_DIR)
    except (ValueError, OSError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        return back("/backups", f"Backup nicht lesbar: {str(exc)[:200]}", False)
    manifest = preview["manifest"]
    live_warnings = "".join(f"<li>{esc(w)}</li>" for w in preview.get("warnings", []))
    changed = [f for f in preview["files"] if f["status"] != "unverändert"]
    file_rows = "".join(f'<tr><td class="py-1 pr-3 font-mono break-all">{esc(f["name"])}</td><td>{esc(f["status"])}</td></tr>' for f in changed[:300])
    table_rows = "".join(f'<tr><td class="py-1 pr-3 font-mono">{esc(t["db"])} · {esc(t["table"])}</td><td>{esc(t["current"])} → {esc(t["backup"])}</td></tr>' for t in preview["tables"][:300])
    problems = "".join(f"<li>{esc(p)}</li>" for p in preview["problems"])
    problem_box = (f'<div class="rounded-xl border border-rose-500/40 bg-rose-500/5 p-3 text-xs"><strong>Backup fehlerhaft – Wiederherstellung gesperrt:</strong><ul class="list-disc pl-5">{problems}</ul></div>'
                   if problems else "")
    if live_warnings:
        problem_box += (f'<div class="rounded-xl border border-amber-500/40 bg-amber-500/5 p-3 text-xs"><strong>Aktueller Stand:</strong>'
                        f'<ul class="list-disc pl-5">{live_warnings}</ul></div>')
    restore_form = "" if problems else f"""
        <form action="/backup/full/restore" method="post" class="{CARD} p-4 flex flex-col gap-3" onsubmit="return window.confirm('Pulse-Daten jetzt aus diesem Backup wiederherstellen?');">
            <input type="hidden" name="filename" value="{esc(os.path.basename(path))}">
            <p class="text-xs text-slate-500 dark:text-slate-400">Vorher wird automatisch eine Sicherheitskopie des aktuellen Stands angelegt. Datenbanken werden in die laufenden Dateien kopiert und auf Integrität geprüft; Uploads werden ergänzt, aber nichts gelöscht. Die Discord-Struktur (Rollen/Kanäle) wird hier <strong>nicht</strong> verändert.</p>
            <label class="text-xs font-semibold">Zur Bestätigung <code>WIEDERHERSTELLEN</code> eingeben<input name="confirm" class="{INPUT} mt-1" autocomplete="off" required></label>
            <button class="self-start bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-4 py-2 rounded-xl font-semibold">↩️ Pulse-Daten wiederherstellen</button>
        </form>"""
    skipped = len(manifest.get("skipped", []))
    body = f"""
    <div class="max-w-4xl space-y-4">
        <a href="/backups" class="text-xs text-indigo-500">← Backups</a>
        <h1 class="text-2xl font-bold">Probelauf: {esc(os.path.basename(path))}</h1>
        <p class="text-xs text-slate-500">Erstellt {esc(display(manifest.get("created_at")))} mit Pulse {esc(manifest.get("pulse_version"))} · {len(manifest.get("files", []))} Dateien · {skipped} wegen Größenbudget/Fehler nicht enthalten.</p>
        {problem_box}
        <div class="{CARD} p-4"><h2 class="font-bold text-sm mb-2">Tabellen mit anderer Zeilenzahl (jetzt → Backup)</h2>
            <table class="text-xs w-full">{table_rows or '<tr><td class="text-slate-400">Keine Unterschiede in den Zeilenzahlen.</td></tr>'}</table></div>
        <div class="{CARD} p-4"><h2 class="font-bold text-sm mb-2">Dateien, die sich ändern oder neu entstehen ({len(changed)})</h2>
            <table class="text-xs w-full">{file_rows or '<tr><td class="text-slate-400">Keine Dateiänderungen.</td></tr>'}</table></div>
        {restore_form}
    </div>"""
    return render_page("Backup-Probelauf", ctx, "backups", body)


@app.post("/backup/full/restore")
async def full_backup_restore(request: Request, filename: str = Form(...), confirm: str = Form(""),
                              user_session: str = Cookie(None)):
    import pulse_backup
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    if confirm.strip() != "WIEDERHERSTELLEN":
        return back(f"/backup/full/preview/{os.path.basename(path)}", "Bitte zur Bestätigung WIEDERHERSTELLEN eingeben.", False)
    actor = ctx.user.get("global_name") or ctx.user.get("username")
    log_audit(actor, ctx.user["id"], "Komplett-Wiederherstellung gestartet", os.path.basename(path))
    try:
        # The JSON I/O lock is held while files are written, so no load-modify-save can interleave.
        report = await asyncio.to_thread(pulse_backup.restore_full_backup, path, DATA_DIR, full_backup_dir(), lock=_io_lock)
    except Exception as exc:
        log_audit(actor, ctx.user["id"], "Komplett-Wiederherstellung fehlgeschlagen", f"{type(exc).__name__}: {str(exc)[:200]}")
        return back("/backups", f"Wiederherstellung fehlgeschlagen: {str(exc)[:240]}", False)
    details = (f"{os.path.basename(path)} · Sicherheitskopie {report['safety_backup']} · DBs: {', '.join(report['databases'])} · "
               f"JSON: {len(report['json'])} · Dateien: {report['files']}")
    log_audit(actor, ctx.user["id"], "Komplett-Wiederherstellung abgeschlossen", details)
    return back("/backups", f"Pulse-Daten wiederhergestellt ({len(report['databases'])} Datenbanken, {len(report['json'])} JSON-Dateien, "
                            f"{report['files']} Dateien). Sicherheitskopie: {report['safety_backup']}. "
                            "Bitte den Bot jetzt neu starten, damit Module mit Zwischenspeicher (Social Media, KI-Moderation) "
                            "die wiederhergestellten Daten laden.")


@app.post("/backup/full/delete")
async def full_backup_delete(request: Request, filename: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    import pulse_server_control  # Server Control Center: never delete the only working backup
    if not await asyncio.to_thread(pulse_server_control.other_verified_backup_exists, full_backup_dir(), os.path.basename(path)):
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup löschen abgelehnt",
                  f"{os.path.basename(path)} · einzige geprüfte Sicherung")
        return back("/backups", "Nicht gelöscht: Das ist die einzige Komplett-Sicherung, die die Prüfsummenkontrolle besteht. "
                                "Erst eine neue Sicherung erstellen.", False)
    os.remove(path)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup gelöscht", os.path.basename(path))
    return back("/backups", "Komplett-Backup gelöscht.")


