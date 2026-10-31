"""Discord commands for the explicit, opt-in backup-server recovery flow."""
from __future__ import annotations

import asyncio
import time

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from pulse_recovery import TOKEN_TIMEOUT

RECOVERY_PAGE_SIZE = 100


UNLOAD_GRACE_SECONDS = 5.0


class RecoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_sync_by_user: dict[int, float] = {}
        self._sync_task: asyncio.Task | None = None
        self._sync_status: dict = {"has_run": False, "running": False}
        # SEC-11: a limited run must not overlap another run – the same refresh token used twice is
        # rotated by Discord, the second use fails.
        self._limited_sync_running = False
        self._stop_requested = False  # Phase 9: set by cog_unload – the full sync ends between two users

    async def cog_unload(self) -> None:
        """Phase 9: a running full sync must not outlive the cog (reload/shutdown). It stops between two users,
        so no refresh token is cut off mid-exchange; only if that takes too long the task is cancelled."""
        task = self._sync_task
        if task is None or task.done():
            return
        self._stop_requested = True
        done, _ = await asyncio.wait({task}, timeout=UNLOAD_GRACE_SECONDS)
        if not done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @app_commands.command(
        name="recovery-sync",
        description="[Admin] Fügt freiwillig freigegebene Nutzer dem Backup-Server hinzu",
    )
    @app_commands.describe(
        confirm="Muss auf True gesetzt werden, damit ein Wiederherstellungs-Sync ausgeführt wird",
        max_users="Maximalzahl für einen begrenzten Lauf (1–100); bei all_users=True ignoriert",
        all_users="True verarbeitet alle freiwillig freigegebenen Konten im Hintergrund",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def recovery_sync(
        self,
        interaction: discord.Interaction,
        confirm: bool = False,
        max_users: app_commands.Range[int, 1, 100] = 25,
        all_users: bool = False,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Dieser Befehl funktioniert nur in einem Discord-Server.", ephemeral=True
            )
            return
        import pulse_recovery
        import webserver as ws

        settings = pulse_recovery.load_settings()
        allowed_guild_ids = {str(ws.GUILD_ID), settings["backup_guild_id"]}
        if str(interaction.guild.id) not in allowed_guild_ids:
            await interaction.response.send_message(
                "Dieser Befehl ist nur im Hauptserver oder im konfigurierten Backup-Server erlaubt.",
                ephemeral=True,
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "⚠️ Es wurde nichts geändert. Prüfe Zielserver und Bürgerrolle. Starte danach erneut mit "
                "confirm=True. Der Befehl entfernt niemanden aus dem Hauptserver und vergibt keine Team- "
                "oder Moderationsrollen.",
                ephemeral=True,
            )
            return
        if not settings["enabled"]:
            await interaction.response.send_message(
                "Die Backup-Wiederherstellung ist deaktiviert.", ephemeral=True
            )
            return
        if (self._sync_task is not None and not self._sync_task.done()) or self._limited_sync_running:
            await interaction.response.send_message(
                "Es läuft bereits ein Recovery-Sync. Nutze /recovery-status, um den Fortschritt zu prüfen.",
                ephemeral=True,
            )
            return
        now = time.monotonic()
        if now - self._last_sync_by_user.get(interaction.user.id, -60.0) < 60:
            await interaction.response.send_message(
                "Bitte warte mindestens 60 Sekunden zwischen zwei Recovery-Syncs.", ephemeral=True
            )
            return

        target, role, problems = pulse_recovery.target_status(self.bot, settings)
        if problems:
            await interaction.response.send_message(
                "❌ Backup-Server nicht einsatzbereit: " + " ".join(problems), ephemeral=True
            )
            return
        self._last_sync_by_user[interaction.user.id] = now

        if all_users:
            total, snapshot_at = pulse_recovery.recovery_snapshot()
            await interaction.response.send_message(
                f"🛡️ **Recovery-Sync im Hintergrund gestartet.** Ziel: **{target.name}**, "
                f"Bürgerrolle: **{role.name}**. Zu Beginn waren {total} Recovery-Konten gespeichert. "
                "Alle freiwillig autorisierten Konten werden seitenweise verarbeitet; es gibt kein "
                "100-Nutzer-Gesamtlimit. Nutze /recovery-status für den Fortschritt. "
                "Je nach Menge und Discord-Rate-Limits kann das länger dauern. "
                "Nur die konfigurierte Bürgerrolle wird vergeben.",
                ephemeral=True,
            )
            self._sync_status = {
                "has_run": True,
                "running": True,
                "actor_name": getattr(interaction.user, "display_name", str(interaction.user.id)),
                "actor_id": str(interaction.user.id),
                "target": target.name,
                "role": role.name,
                "target_id": str(target.id),
                "role_id": str(role.id),
                "total": total,
                "scanned": 0,
                "added": 0,
                "already": 0,
                "failed": 0,
                "failures": [],
                "started_at": time.time(),
                "finished_at": None,
                "stopped_reason": "",
            }
            self._sync_task = asyncio.create_task(
                self._run_all_sync(dict(settings), target, role, snapshot_at),
                name="pulse-recovery-sync-all",
            )
            return

        self._limited_sync_running = True
        try:
            await self._run_limited_sync(interaction, pulse_recovery, ws, settings, target, role, max_users)
        finally:
            self._limited_sync_running = False

    async def _run_limited_sync(self, interaction, pulse_recovery, ws, settings, target, role, max_users):
        await interaction.response.defer(ephemeral=True, thinking=True)
        people = pulse_recovery.list_linked_users(int(max_users))
        if not people:
            await interaction.followup.send(
                "Es gibt keine gültigen, ausdrücklich freigegebenen Recovery-Konten. "
                "Nutzer müssen den Backup-Zugang selbst autorisieren.",
                ephemeral=True,
            )
            return

        added = 0
        already = 0
        failed = 0
        failures: list[str] = []
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
            for index, person in enumerate(people):
                ok, message = await pulse_recovery.rejoin_user(
                    person["user_id"], person["refresh_token"], self.bot, client, settings=settings
                )
                if ok:
                    if message.startswith("bereits vorhanden"):
                        already += 1
                    else:
                        added += 1
                else:
                    failed += 1
                    if len(failures) < 5:
                        failures.append(f"{person['user_id']}: {message}")
                    if message == pulse_recovery.CONFIG_ERROR_MESSAGE:
                        break  # SEC-11: wrong client credentials – stop instead of failing every user
                if index + 1 < len(people):
                    await asyncio.sleep(1.0)

        self._write_sync_audit(
            ws,
            getattr(interaction.user, "display_name", str(interaction.user.id)),
            str(interaction.user.id),
            target,
            role,
            len(people),
            added,
            already,
            failed,
        )
        lines = [
            "🛡️ **Recovery-Sync abgeschlossen**",
            f"Geprüft: {len(people)} · Neu hinzugefügt: {added} · Bereits vorhanden: {already} · Fehler: {failed}",
            f"Ziel: **{target.name}** · vergeben wird ausschließlich **{role.name}**.",
            "Es werden keine Mitglieder aus dem Hauptserver gekickt und keine bestehenden Rollen entfernt. "
            "Neue Mitglieder erhalten keine Rollen aus dem Hauptserver.",
        ]
        if failures:
            lines.append("Einige Fehler: " + " | ".join(failures))
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    async def _run_all_sync(self, settings: dict, target, role, snapshot_at: float) -> None:
        """Process every explicitly opted-in user in stable database pages."""
        import pulse_recovery
        import webserver as ws

        state = self._sync_status
        cursor: str | None = None
        try:
            async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
                while True:
                    current = pulse_recovery.load_settings()
                    if (
                        not current["enabled"]
                        or current["backup_guild_id"] != settings["backup_guild_id"]
                        or current["citizen_role_id"] != settings["citizen_role_id"]
                    ):
                        state["stopped_reason"] = (
                            "Backup-Konfiguration wurde deaktiviert oder geändert; "
                            "der Lauf wurde aus Sicherheitsgründen gestoppt."
                        )
                        break

                    people, next_cursor, scanned_page, has_more = pulse_recovery.list_linked_users_page(
                        after_user_id=cursor,
                        limit=RECOVERY_PAGE_SIZE,
                        consented_before=snapshot_at,
                    )
                    if scanned_page == 0:
                        break
                    invalid_tokens = scanned_page - len(people)
                    if invalid_tokens:
                        state["scanned"] += invalid_tokens
                        state["failed"] += invalid_tokens
                        state["failures"].append(
                            f"{invalid_tokens} gespeicherte(r) Zugang/Zugänge konnten nicht entschlüsselt werden."
                        )
                        state["failures"] = state["failures"][:5]

                    for index, person in enumerate(people):
                        if self._stop_requested:
                            state["stopped_reason"] = "Pulse wurde beendet oder neu geladen. Starte den Sync bei Bedarf erneut."
                            break
                        current = pulse_recovery.load_settings()
                        if (
                            not current["enabled"]
                            or current["backup_guild_id"] != settings["backup_guild_id"]
                            or current["citizen_role_id"] != settings["citizen_role_id"]
                        ):
                            state["stopped_reason"] = (
                                "Backup-Konfiguration wurde deaktiviert oder geändert; "
                                "der Lauf wurde aus Sicherheitsgründen gestoppt."
                            )
                            break
                        ok, message = await pulse_recovery.rejoin_user(
                            person["user_id"], person["refresh_token"], self.bot, client, settings=settings
                        )
                        state["scanned"] += 1
                        if ok:
                            if message.startswith("bereits vorhanden"):
                                state["already"] += 1
                            else:
                                state["added"] += 1
                        else:
                            state["failed"] += 1
                            if len(state["failures"]) < 5:
                                state["failures"].append(f"{person['user_id']}: {message}")
                            if message == pulse_recovery.CONFIG_ERROR_MESSAGE:
                                state["stopped_reason"] = message
                                break
                        if index + 1 < len(people):
                            await asyncio.sleep(1.0)

                    cursor = next_cursor
                    if state["stopped_reason"] or not has_more:
                        break
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            state["stopped_reason"] = "Der Bot hat den Sync unterbrochen. Starte ihn bei Bedarf erneut."
            raise
        except Exception as exc:
            state["stopped_reason"] = f"Unerwarteter Fehler: {type(exc).__name__}"
        finally:
            state["running"] = False
            state["finished_at"] = time.time()
            self._write_sync_audit(
                ws,
                state["actor_name"],
                state["actor_id"],
                target,
                role,
                state["scanned"],
                state["added"],
                state["already"],
                state["failed"],
            )

    @staticmethod
    def _write_sync_audit(ws, actor_name, actor_id, target, role, scanned, added, already, failed) -> None:
        try:
            ws.log_audit(
                actor_name,
                str(actor_id),
                "Backup-Recovery-Sync ausgeführt",
                f"Backup-Guild {target.id}; Bürgerrolle {role.id}; geprüft {scanned}; "
                f"neu {added}; vorhanden {already}; Fehler {failed}",
            )
        except Exception:
            pass

    @app_commands.command(
        name="recovery-status",
        description="[Admin] Zeigt den Fortschritt des vollständigen Recovery-Syncs",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def recovery_status(self, interaction: discord.Interaction):
        state = self._sync_status
        if not state.get("has_run"):
            await interaction.response.send_message(
                "In dieser Bot-Laufzeit wurde noch kein vollständiger Recovery-Sync gestartet.",
                ephemeral=True,
            )
            return
        started_at = float(state.get("started_at") or time.time())
        elapsed = max(0, int((time.time() - started_at) if state.get("running") else (
            (state.get("finished_at") or time.time()) - started_at
        )))
        status = "🟡 Läuft" if state.get("running") else "✅ Beendet"
        lines = [
            f"**Recovery-Sync: {status}**",
            f"Ziel: **{state.get('target', 'unbekannt')}** · Bürgerrolle: **{state.get('role', 'unbekannt')}**",
            f"Verarbeitet/gescannt: {state.get('scanned', 0)} von anfangs {state.get('total', 0)}",
            f"Neu: {state.get('added', 0)} · Bereits vorhanden: {state.get('already', 0)} · Fehler: {state.get('failed', 0)}",
            f"Laufzeit: {elapsed // 60} Min. {elapsed % 60} Sek.",
        ]
        if state.get("stopped_reason"):
            lines.append(f"Hinweis: {state['stopped_reason']}")
        if state.get("failures"):
            lines.append("Fehlerbeispiele: " + " | ".join(state["failures"][:5]))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="recovery-abmelden",
        description="Beendet deine freiwillige Pulse-Backup-Freigabe und löscht den gespeicherten Recovery-Zugang",
    )
    async def recovery_unlink(self, interaction: discord.Interaction):
        import pulse_recovery
        import webserver as ws
        uid = str(interaction.user.id)
        refresh_token = pulse_recovery.get_refresh_token(uid)
        if not refresh_token:
            await interaction.response.send_message(
                "Für dein Konto ist keine aktive Recovery-Freigabe gespeichert.", ephemeral=True
            )
            return
        revoked = False
        try:
            async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
                response = await client.post(
                    "https://discord.com/api/v10/oauth2/token/revoke",
                    data={
                        "client_id": ws.CLIENT_ID,
                        "client_secret": ws.CLIENT_SECRET,
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                revoked = response.status_code in (200, 204)
        except httpx.HTTPError:
            pass
        pulse_recovery.unlink(uid)
        try:
            ws.log_audit(
                getattr(interaction.user, "display_name", uid),
                uid,
                "Backup-Recovery-Freigabe widerrufen",
                "Gespeicherter OAuth-Recovery-Zugang gelöscht.",
            )
        except Exception:
            pass
        status = "Discord-Freigabe widerrufen" if revoked else "lokaler Recovery-Zugang gelöscht"
        await interaction.response.send_message(
            f"✅ Deine Recovery-Freigabe wurde beendet ({status}). Pulse wird dich nicht mehr automatisch "
            "zum Backup-Server hinzufügen. Falls du den Backup-Server ebenfalls verlassen möchtest, "
            "kannst du ihn zusätzlich selbst verlassen.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecoveryCog(bot))
