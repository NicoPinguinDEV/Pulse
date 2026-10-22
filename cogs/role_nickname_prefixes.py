"""Apply configurable role-based prefixes to Discord server nicknames."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import discord
from discord.ext import commands

from pulse_nickname_utils import (
    ABSENCE_SUFFIX,
    automatic_role_prefix,
    format_prefixed_nickname,
    looks_like_date_only,
    normalize_role_prefix,
    select_highest_role_prefix,
    split_absence_suffix,
    strip_known_prefix,
    strip_legacy_rank_prefix,
    strip_pulse_prefixes,
)
from webserver import GUILD_ID, load_config

STATE_FILE = Path(__file__).resolve().parents[1] / "role_nickname_state.json"
MAX_CONFIGURED_PREFIX_ROLES = 150


class RoleNicknamePrefixes(commands.Cog):
    """Apply the highest-priority configured role prefix on join and role changes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._member_locks: dict[int, asyncio.Lock] = {}
        self._state_cache_lock = asyncio.Lock()
        self._state_file_lock = asyncio.Lock()
        self._member_state_cache: dict[str, dict] | None = None
        self._state_revision = 0
        self._state_dirty = False
        self._bulk_state_batching = False
        self._bulk_sync_task: asyncio.Task | None = None
        self._startup_sync_started = False
        self._sync_requested_again = False

    @staticmethod
    def _read_state_file_sync() -> dict[str, dict]:
        """Load the nickname state once; preserve a damaged file for recovery."""
        if not STATE_FILE.exists():
            return {}
        try:
            with STATE_FILE.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                return {
                    str(key): value
                    for key, value in raw.items()
                    if isinstance(value, dict)
                }
            raise ValueError("Die Zustandsdatei enthält kein JSON-Objekt.")
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as exc:
            backup = STATE_FILE.with_name(
                f"{STATE_FILE.stem}.corrupt-{time.time_ns()}{STATE_FILE.suffix}"
            )
            try:
                os.replace(STATE_FILE, backup)
                print(
                    f"⚠️ Namenspräfix-Zustandsdatei war ungültig und wurde zur Wiederherstellung "
                    f"gesichert: {backup.name} ({type(exc).__name__}: {exc})"
                )
            except OSError as backup_exc:
                print(
                    f"❌ Namenspräfix-Zustandsdatei konnte nicht gelesen/gesichert werden: "
                    f"{type(exc).__name__}: {exc}; Sicherung fehlgeschlagen: {backup_exc}"
                )
            return {}

    @staticmethod
    def _write_state_file_sync(states: dict[str, dict]) -> None:
        """Write to a sibling temporary file and atomically replace the state file."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = STATE_FILE.with_name(
            f"{STATE_FILE.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(states, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, STATE_FILE)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _ensure_state_cache(self) -> None:
        if self._member_state_cache is not None:
            return
        async with self._state_cache_lock:
            if self._member_state_cache is None:
                self._member_state_cache = await asyncio.to_thread(self._read_state_file_sync)

    async def _get_member_state(self, member_id: int) -> dict | None:
        await self._ensure_state_cache()
        async with self._state_cache_lock:
            states = self._member_state_cache or {}
            state = states.get(str(member_id))
            return dict(state) if isinstance(state, dict) else None

    async def _set_member_state(self, member_id: int, state: dict | None) -> None:
        await self._ensure_state_cache()
        async with self._state_cache_lock:
            if self._member_state_cache is None:
                self._member_state_cache = {}
            key = str(member_id)
            if state is None:
                self._member_state_cache.pop(key, None)
            else:
                self._member_state_cache[key] = dict(state)
            self._state_revision += 1
            self._state_dirty = True

        if not self._bulk_state_batching:
            await self._flush_state_cache()

    async def _flush_state_cache(self) -> None:
        async with self._state_file_lock:
            await self._ensure_state_cache()
            async with self._state_cache_lock:
                if not self._state_dirty or self._member_state_cache is None:
                    return
                snapshot = {
                    key: dict(value)
                    for key, value in self._member_state_cache.items()
                    if isinstance(value, dict)
                }
                revision = self._state_revision
            await asyncio.to_thread(self._write_state_file_sync, snapshot)
            async with self._state_cache_lock:
                if revision == self._state_revision:
                    self._state_dirty = False

    @staticmethod
    def _valid_prefix_map(config: dict) -> dict[str, str]:
        raw = config.get("role_nickname_prefixes", {})
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, str] = {}
        for raw_role_id, raw_prefix in raw.items():
            if not str(raw_role_id).isdigit():
                continue
            try:
                cleaned[str(int(raw_role_id))] = normalize_role_prefix(raw_prefix)
            except ValueError:
                continue
        return cleaned

    @staticmethod
    def _selected_role(member: discord.Member, prefixes: dict[str, str]):
        """Highest Discord role always wins; custom settings only override its prefix."""
        selected = select_highest_role_prefix(
            [
                (role.id, role.position, role.name, role.is_default())
                for role in member.roles
            ],
            prefixes,
        )
        if selected is None:
            return None
        role_id, prefix = selected
        role = next((candidate for candidate in member.roles if candidate.id == role_id), None)
        return (role, prefix) if role is not None else None

    async def _is_user_abgemeldet(self, member: discord.Member) -> bool:
        """Use the bot's absence database as the sole authority for the suffix."""
        # Unit helpers may instantiate the cog without a bot; retain their
        # historical suffix-based behavior without weakening production checks.
        if self.bot is None:
            return bool(split_absence_suffix(member.nick)[1])

        get_cog = getattr(self.bot, "get_cog", None)
        absence_cog = get_cog("AbmeldungCog") if callable(get_cog) else None
        checker = getattr(absence_cog, "is_abgemeldet", None)
        if callable(checker):
            try:
                return bool(await asyncio.to_thread(checker, member.id))
            except Exception as exc:
                print(f"⚠️ Abmeldestatus für {member.id} konnte nicht geprüft werden: {type(exc).__name__}: {exc}")

        # Startup/event ordering can briefly call the nickname cog before the
        # absence cog is registered. Read its existing SQLite source of truth.
        try:
            def _lookup_absence_record() -> bool:
                import sqlite3
                from cogs.abmeldung import DB_NAME

                connection = sqlite3.connect(DB_NAME, timeout=2.0)
                try:
                    return connection.execute(
                        "SELECT 1 FROM abmeldungen WHERE user_id = ? LIMIT 1",
                        (member.id,),
                    ).fetchone() is not None
                finally:
                    connection.close()

            return await asyncio.to_thread(_lookup_absence_record)
        except Exception as exc:
            # Fail closed: a stale nickname suffix must not impersonate an
            # active bot-managed absence if the authoritative record is unreadable.
            print(f"⚠️ Abmeldedatenbank für {member.id} nicht lesbar: {type(exc).__name__}: {exc}")
            return False

    async def _synchronize_member_locked(
        self,
        member: discord.Member,
        *,
        config_snapshot: dict | None = None,
        nickname_changed: bool = False,
    ) -> str:
        if member.bot or (GUILD_ID and member.guild.id != GUILD_ID):
            return "unchanged"

        config = config_snapshot if isinstance(config_snapshot, dict) else await asyncio.to_thread(load_config)
        prefixes = self._valid_prefix_map(config)
        state = await self._get_member_state(member.id)
        current_nick = member.nick
        # The suffix is not a user-editable label: an actual Abmeldung DB record
        # decides whether it is present. This prevents manual suffixes and keeps
        # it after a teamler changes their own nickname while absent.
        is_absent = await self._is_user_abgemeldet(member)
        absence_suffix = ABSENCE_SUFFIX if is_absent else ""
        saved_prefix = str(state.get("applied_prefix") or "") if state else ""
        still_has_our_prefix = bool(
            current_nick is not None
            and saved_prefix
            and current_nick.startswith(f"{saved_prefix} ")
        )
        has_recorded_applied_nick = bool(state and "last_applied_nick" in state)
        is_manual_nickname_change = bool(
            nickname_changed
            and (
                not has_recorded_applied_nick
                or current_nick != state.get("last_applied_nick")
            )
        )
        own_prefixes = list(prefixes.values()) + [config.get("nickname_default_prefix", "Bürger ✗")]
        selected = self._selected_role(member, prefixes)
        if selected is None and current_nick:
            # After a bot re-invite, a partially filled Discord member cache can
            # temporarily expose only @everyone. If the existing nickname carries
            # a rank tag, refresh that member before considering the citizen default.
            legacy_base = strip_legacy_rank_prefix(current_nick)
            has_legacy_rank_tag = bool(legacy_base and legacy_base != current_nick)
            only_default_role = not any(not role.is_default() for role in member.roles)
            if has_legacy_rank_tag and only_default_role:
                try:
                    refreshed_member = await member.guild.fetch_member(member.id)
                    if refreshed_member.roles:
                        member = refreshed_member
                        current_nick = member.nick
                        selected = self._selected_role(member, prefixes)
                except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                    print(
                        f"⚠️ Rollen für {member.id} konnten nicht neu geladen werden: "
                        f"{type(exc).__name__}: {exc}"
                    )
                if selected is None:
                    # Better leave the nickname untouched than downgrade a known
                    # rank to Bürger just because role data is missing.
                    print(
                        f"⏸️ Namenspräfix übersprungen für {member} ({member.id}): "
                        "Discord liefert derzeit nur @everyone, obwohl der Name eine Rangkennung enthält."
                    )
                    return "deferred"

        if selected is None:
            # The standard citizen prefix applies to new and existing members
            # even before an automatic verification role is assigned.
            raw_default_prefix = config.get("nickname_default_prefix", "Bürger ✗")
            try:
                default_prefix = normalize_role_prefix(raw_default_prefix) if str(raw_default_prefix or "").strip() else ""
            except ValueError:
                default_prefix = ""
            if default_prefix:
                selected = (None, default_prefix)

        if selected:
            role, prefix = selected
            role_label = role.name if role else "Standardpräfix"
            role_id = str(role.id) if role else "default"
            if state and is_manual_nickname_change:
                # A teamler can rename themselves in Discord. Strip the managed
                # rank/absence decoration and save the edited name as the new base,
                # even when the visible rank prefix itself did not change.
                edited_base = split_absence_suffix(current_nick)[0] or member.name
                edited_base = strip_pulse_prefixes(edited_base, own_prefixes)
                edited_base = strip_legacy_rank_prefix(edited_base)
                original_nick = edited_base or member.name
                base_name = original_nick
            elif state and still_has_our_prefix:
                original_nick = state.get("original_nick")
                base_name = original_nick if isinstance(original_nick, str) and original_nick else member.name
            elif state:
                # If the user replaced the whole nickname, preserve it as the base.
                original_nick = current_nick
                base_name = current_nick or member.name
            else:
                # Avoid duplicating prefixes if a nickname was already formatted
                # before state was first recorded (for example after a restore).
                known_prefixes = list(prefixes.values()) + [
                    config.get("nickname_default_prefix", "Bürger ✗")
                ]
                known_prefixes.extend(
                    automatic_role_prefix(role.name)
                    for role in member.roles
                    if not role.is_default()
                )
                stripped = strip_known_prefix(current_nick, known_prefixes)
                if stripped:
                    original_nick = stripped
                    base_name = stripped
                else:
                    original_nick = current_nick
                    base_name = current_nick or member.name

            # Older nicknames may already contain a manual rank tag, e.g.
            # 'Sr.-Sup メ Dawo' or 'Owner メ Phillip'. Remove it before applying
            # the real highest-role prefix, including from saved nickname state.
            # Pulse's own prefixes of a previous rank ('L.Mod ✗') are removed too,
            # otherwise a rank change stacked them ('Jr.Admin ✗ L.Mod ✗ Dawo').
            def _clean(value):
                value = split_absence_suffix(value)[0]
                value = strip_pulse_prefixes(value, own_prefixes)
                value = strip_legacy_rank_prefix(value)
                # A bare date is a damaged absence restore, never a real name.
                return None if not value or looks_like_date_only(value) else value

            cleaned_base_name = _clean(base_name) or member.name
            if isinstance(original_nick, str) and original_nick.strip():
                original_nick = _clean(original_nick) or member.name
            base_name = cleaned_base_name

            try:
                target_nick = format_prefixed_nickname(prefix, base_name, absence_suffix)
            except ValueError as exc:
                print(f"⚠️ Namenspräfix für {role_label} ({role_id}) ungültig: {exc}")
                return "failed"

            new_state = {
                "original_nick": original_nick,
                "applied_prefix": prefix,
                "role_id": role_id,
                "last_applied_nick": target_nick,
            }
            if current_nick == target_nick:
                if state != new_state:
                    await self._set_member_state(member.id, new_state)
                return "unchanged"

            # Persist state before editing so an immediate gateway event can
            # identify the nickname as bot-managed. Revert state if Discord rejects it.
            await self._set_member_state(member.id, new_state)
            try:
                await member.edit(
                    nick=target_nick,
                    reason=f"Pulse: automatischer Namenspräfix für {role_label}",
                )
                print(
                    f"🏷️ Namenspräfix angewendet: {member} ({member.id}) · {role_label} · "
                    f"{current_nick or '(kein Spitzname)'} → {target_nick}"
                )
                return "updated"
            except discord.Forbidden:
                await self._set_member_state(member.id, state)
                print(
                    f"⚠️ Namenspräfix für {member.id} nicht erlaubt. "
                    "Prüfe 'Spitznamen verwalten' und die Rollen-Hierarchie des Bots."
                )
                return "failed"
            except (discord.HTTPException, discord.NotFound) as exc:
                await self._set_member_state(member.id, state)
                print(f"⚠️ Namenspräfix für {member.id} fehlgeschlagen: {type(exc).__name__}: {exc}")
                return "failed"

        if not state:
            current_base, current_suffix = split_absence_suffix(current_nick)
            if current_nick is not None and current_suffix and not is_absent:
                target_nick = current_base
                if target_nick != current_nick:
                    try:
                        await member.edit(nick=target_nick, reason="Pulse: ungültigen Abmeldezusatz entfernen")
                    except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                        print(f"⚠️ Abmeldezusatz für {member.id} konnte nicht korrigiert werden: {type(exc).__name__}: {exc}")
                        return "failed"
                    return "updated"
            if is_absent and current_nick is not None and not current_suffix:
                base_name = current_nick or member.name
                target_nick = f"{base_name[: 32 - len(absence_suffix)].rstrip()}{absence_suffix}"
                try:
                    await member.edit(nick=target_nick, reason="Pulse: Abmeldezusatz wiederherstellen")
                    return "updated"
                except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                    print(f"⚠️ Abmeldezusatz für {member.id} konnte nicht gesetzt werden: {type(exc).__name__}: {exc}")
                    return "failed"
            return "unchanged"

        if is_manual_nickname_change:
            # Prefix mapping was removed while the user edited their name. Do not
            # restore an older name from state; keep the edit and only normalize the
            # absence suffix to match the current database state.
            base_name = split_absence_suffix(current_nick)[0] or member.name
            base_name = strip_pulse_prefixes(base_name, own_prefixes) or base_name
            base_name = strip_legacy_rank_prefix(base_name) or base_name
            target_nick = f"{base_name[: 32 - len(absence_suffix)].rstrip()}{absence_suffix}"
            if current_nick != target_nick:
                try:
                    await member.edit(nick=target_nick, reason="Pulse: manuelle Namensänderung beibehalten")
                except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                    print(f"⚠️ Manuelle Namensänderung für {member.id} konnte nicht normalisiert werden: {type(exc).__name__}: {exc}")
                    return "failed"
            await self._set_member_state(member.id, None)
            return "updated" if current_nick != target_nick else "unchanged"

        if still_has_our_prefix:
            original_nick = state.get("original_nick")
            if absence_suffix:
                core = str(original_nick or member.name)
                original_nick = core[: 32 - len(absence_suffix)].rstrip() + absence_suffix
            try:
                if current_nick != original_nick:
                    await member.edit(
                        nick=original_nick,
                        reason="Pulse: Rolle mit Namenspräfix entfernt",
                    )
            except discord.Forbidden:
                print(
                    f"⚠️ Ursprünglicher Nickname von {member.id} konnte nicht wiederhergestellt werden. "
                    "Prüfe 'Spitznamen verwalten' und die Rollen-Hierarchie."
                )
                return "failed"
            except (discord.HTTPException, discord.NotFound) as exc:
                print(f"⚠️ Nickname-Wiederherstellung für {member.id} fehlgeschlagen: {exc}")
                return "failed"

            await self._set_member_state(member.id, None)
            print(f"🏷️ Namenspräfix entfernt: {member} ({member.id})")
            return "restored"

        # The nickname no longer starts with our recorded prefix, so a human
        # changed it manually. Never revert that manual edit.
        await self._set_member_state(member.id, None)
        return "unchanged"

    async def synchronize_member(
        self,
        member: discord.Member,
        *,
        config_snapshot: dict | None = None,
        nickname_changed: bool = False,
    ) -> str:
        lock = self._member_locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            return await self._synchronize_member_locked(
                member,
                config_snapshot=config_snapshot,
                nickname_changed=nickname_changed,
            )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Backfill existing members once per bot process, not only future joins."""
        if self._startup_sync_started:
            return
        guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else None
        if guild is None:
            print(
                "⚠️ Namenspräfix-Synchronisierung beim Start wartet: "
                "konfigurierter Discord-Server ist noch nicht im Cache."
            )
            return
        self._startup_sync_started = True
        if self.start_sync_existing_members():
            print(
                "🏷️ Automatische Synchronisierung bestehender Mitgliedernamen "
                "beim Bot-Start eingereiht."
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not GUILD_ID or member.guild.id == GUILD_ID:
            await self.synchronize_member(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.guild.id != after.guild.id:
            return
        if before.roles != after.roles or before.nick != after.nick:
            await self.synchronize_member(
                after,
                nickname_changed=before.nick != after.nick,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not GUILD_ID or member.guild.id == GUILD_ID:
            await self._set_member_state(member.id, None)

    def start_sync_existing_members(self, *, queue_if_running: bool = False) -> bool:
        if self._bulk_sync_task and not self._bulk_sync_task.done():
            if queue_if_running:
                # Configuration may change while a pass is in progress. Queue one
                # fresh pass so members missed by the old candidate list are caught.
                self._sync_requested_again = True
            return False
        self._sync_requested_again = False
        self._bulk_sync_task = asyncio.create_task(
            self._run_sync_existing_members(), name="pulse-role-nickname-prefix-sync"
        )
        return True

    async def _run_sync_existing_members(self) -> None:
        while True:
            self._sync_requested_again = False
            try:
                await self.sync_existing_members()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    "❌ Namenspräfix-Synchronisierung unerwartet fehlgeschlagen: "
                    f"{type(exc).__name__}: {exc}"
                )
            if not self._sync_requested_again:
                return
            print(
                "🔁 Namenspräfix-Regeln wurden während der Synchronisierung geändert; "
                "eine weitere Synchronisierung wird gestartet."
            )

    async def sync_existing_members(self) -> None:
        guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else None
        if guild is None:
            print("⚠️ Namenspräfix-Synchronisierung abgebrochen: konfigurierte Discord-Server-ID nicht geladen.")
            return

        # Force a fresh member chunk even when discord.py marks a partial cache
        # as chunked. A stale cache here can incorrectly give team leads Bürger ✗.
        try:
            await guild.chunk(cache=True)
            print(f"🏷️ Discord-Mitglieder/Rollen-Cache aktualisiert: {len(guild.members)} Mitglieder geladen.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(
                "⚠️ Discord-Mitgliederliste konnte nicht vollständig geladen werden; "
                f"Synchronisierung arbeitet mit dem verfügbaren Cache: {type(exc).__name__}: {exc}"
            )

        # Load the config and nickname state once for the complete backfill.
        # Synchronous per-member file IO was blocking Discord's gateway heartbeat.
        config = await asyncio.to_thread(load_config)
        prefixes = self._valid_prefix_map(config)
        await self._ensure_state_cache()
        async with self._state_cache_lock:
            states = dict(self._member_state_cache or {})
        raw_default_prefix = config.get("nickname_default_prefix", "Bürger ✗")
        try:
            default_prefix = normalize_role_prefix(raw_default_prefix) if str(raw_default_prefix or "").strip() else ""
        except ValueError:
            default_prefix = ""
        role_ids = set(prefixes)
        candidates = [
            member for member in list(guild.members)
            if not member.bot
            and (
                default_prefix
                or str(member.id) in states
                or any(not role.is_default() for role in member.roles)
                or any(str(role.id) in role_ids for role in member.roles)
            )
        ]
        updated = restored = failed = 0
        print(
            f"🏷️ Namenspräfix-Synchronisierung gestartet: {len(candidates)} passende "
            f"Mitglieder im aktuellen Discord-Cache."
        )
        self._bulk_state_batching = True
        try:
            for index, member in enumerate(candidates, start=1):
                result = await self.synchronize_member(member, config_snapshot=config)
                updated += result == "updated"
                restored += result == "restored"
                failed += result == "failed"
                if index % 25 == 0:
                    await self._flush_state_cache()
                    await asyncio.sleep(0)
                if result in ("updated", "restored"):
                    await asyncio.sleep(0.35)
        finally:
            self._bulk_state_batching = False
            await self._flush_state_cache()
        print(
            f"✅ Namenspräfix-Synchronisierung abgeschlossen: {updated} geändert, "
            f"{restored} zurückgesetzt, {failed} fehlgeschlagen."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoleNicknamePrefixes(bot))
