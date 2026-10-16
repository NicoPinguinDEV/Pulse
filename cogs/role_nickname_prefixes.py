"""Apply configurable role-based prefixes to Discord server nicknames."""
from __future__ import annotations

import asyncio
from pathlib import Path

import discord
from discord.ext import commands

from pulse_nickname_utils import format_prefixed_nickname, normalize_role_prefix, strip_known_prefix
from webserver import GUILD_ID, load_config, load_json, save_json

STATE_FILE = Path(__file__).resolve().parents[1] / "role_nickname_state.json"
MAX_CONFIGURED_PREFIX_ROLES = 150


class RoleNicknamePrefixes(commands.Cog):
    """Apply the highest-priority configured role prefix on join and role changes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._member_locks: dict[int, asyncio.Lock] = {}
        self._state_file_lock = asyncio.Lock()
        self._bulk_sync_task: asyncio.Task | None = None

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
        """Highest Discord role wins when a member has multiple configured roles."""
        matches = [
            (role.position, role.id, role, prefixes[str(role.id)])
            for role in member.roles
            if str(role.id) in prefixes
        ]
        if not matches:
            return None
        _, _, role, prefix = max(matches, key=lambda item: (item[0], item[1]))
        return role, prefix

    async def _get_member_state(self, member_id: int) -> dict | None:
        async with self._state_file_lock:
            states = load_json(STATE_FILE, {})
            state = states.get(str(member_id)) if isinstance(states, dict) else None
            return dict(state) if isinstance(state, dict) else None

    async def _set_member_state(self, member_id: int, state: dict | None) -> None:
        async with self._state_file_lock:
            states = load_json(STATE_FILE, {})
            if not isinstance(states, dict):
                states = {}
            key = str(member_id)
            if state is None:
                states.pop(key, None)
            else:
                states[key] = state
            save_json(STATE_FILE, states)

    async def _synchronize_member_locked(self, member: discord.Member) -> str:
        if member.bot or (GUILD_ID and member.guild.id != GUILD_ID):
            return "unchanged"

        config = load_config()
        prefixes = self._valid_prefix_map(config)
        state = await self._get_member_state(member.id)
        current_nick = member.nick
        saved_prefix = str(state.get("applied_prefix") or "") if state else ""
        still_has_our_prefix = bool(
            current_nick is not None
            and saved_prefix
            and current_nick.startswith(f"{saved_prefix} ")
        )
        selected = self._selected_role(member, prefixes)
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
            if state and still_has_our_prefix:
                original_nick = state.get("original_nick")
                base_name = original_nick if isinstance(original_nick, str) and original_nick else member.name
            elif state:
                # A human changed the nickname since the last automatic update.
                # Keep that new value as the base instead of overwriting it.
                original_nick = current_nick
                base_name = current_nick or member.name
            else:
                # Avoid duplicating prefixes if a nickname was already formatted
                # before state was first recorded (for example after a restore).
                known_prefixes = list(prefixes.values()) + [
                    config.get("nickname_default_prefix", "Bürger ✗")
                ]
                stripped = strip_known_prefix(current_nick, known_prefixes)
                if stripped:
                    original_nick = stripped
                    base_name = stripped
                else:
                    original_nick = current_nick
                    base_name = current_nick or member.name

            try:
                target_nick = format_prefixed_nickname(prefix, base_name)
            except ValueError as exc:
                print(f"⚠️ Namenspräfix für {role_label} ({role_id}) ungültig: {exc}")
                return "failed"

            new_state = {
                "original_nick": original_nick,
                "applied_prefix": prefix,
                "role_id": role_id,
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
                    f"🏷️ Namenspräfix angewendet: {member} ({member.id}) · {role_label} → {target_nick}"
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
            return "unchanged"

        if still_has_our_prefix:
            original_nick = state.get("original_nick")
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

    async def synchronize_member(self, member: discord.Member) -> str:
        lock = self._member_locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            return await self._synchronize_member_locked(member)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not GUILD_ID or member.guild.id == GUILD_ID:
            await self.synchronize_member(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.guild.id != after.guild.id:
            return
        if before.roles != after.roles or before.nick != after.nick:
            await self.synchronize_member(after)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not GUILD_ID or member.guild.id == GUILD_ID:
            await self._set_member_state(member.id, None)

    def start_sync_existing_members(self) -> bool:
        if self._bulk_sync_task and not self._bulk_sync_task.done():
            return False
        self._bulk_sync_task = asyncio.create_task(
            self.sync_existing_members(), name="pulse-role-nickname-prefix-sync"
        )
        return True

    async def sync_existing_members(self) -> None:
        guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else None
        if guild is None:
            print("⚠️ Namenspräfix-Synchronisierung abgebrochen: konfigurierte Discord-Server-ID nicht geladen.")
            return

        config = load_config()
        prefixes = self._valid_prefix_map(config)
        states = load_json(STATE_FILE, {})
        states = states if isinstance(states, dict) else {}
        raw_default_prefix = config.get("nickname_default_prefix", "Bürger ✗")
        try:
            default_prefix = normalize_role_prefix(raw_default_prefix) if str(raw_default_prefix or "").strip() else ""
        except ValueError:
            default_prefix = ""
        if not prefixes and not states and not default_prefix:
            print("🏷️ Namenspräfix-Synchronisierung: Es sind keine Rollenpräfixe oder gespeicherten Nicknames vorhanden.")
            return
        role_ids = set(prefixes)
        candidates = [
            member for member in list(guild.members)
            if not member.bot
            and (
                default_prefix
                or str(member.id) in states
                or any(str(role.id) in role_ids for role in member.roles)
            )
        ]
        updated = restored = failed = 0
        print(
            f"🏷️ Namenspräfix-Synchronisierung gestartet: {len(candidates)} passende "
            f"Mitglieder im aktuellen Discord-Cache."
        )
        for member in candidates:
            result = await self.synchronize_member(member)
            updated += result == "updated"
            restored += result == "restored"
            failed += result == "failed"
            if result in ("updated", "restored"):
                # Space out edits to avoid bursts of Discord nickname changes.
                await asyncio.sleep(0.35)
        print(
            f"✅ Namenspräfix-Synchronisierung abgeschlossen: {updated} geändert, "
            f"{restored} zurückgesetzt, {failed} fehlgeschlagen."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoleNicknamePrefixes(bot))
