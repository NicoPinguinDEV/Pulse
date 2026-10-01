from datetime import datetime
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "abmeldungen.db"


# ---------------------------------------------------------------------------
# UI: Abmeldungsliste mit persistenten Buttons
# ---------------------------------------------------------------------------
class AbmeldungModal(discord.ui.Modal, title="Abmeldung eintragen"):
    grund = discord.ui.TextInput(
        label="Grund",
        placeholder="z. B. Schule, Urlaub, privat ...",
        min_length=2,
        max_length=250,
        required=True,
    )
    von = discord.ui.TextInput(
        label="Von",
        placeholder="TT.MM.JJJJ",
        min_length=10,
        max_length=10,
        required=True,
    )
    bis = discord.ui.TextInput(
        label="Bis",
        placeholder="TT.MM.JJJJ",
        min_length=10,
        max_length=10,
        required=True,
    )

    def __init__(self, cog: "AbmeldungCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.abmeldung_eintragen(
            interaction,
            grund=str(self.grund.value).strip(),
            von=str(self.von.value).strip(),
            bis=str(self.bis.value).strip(),
        )


class AbmeldungView(discord.ui.View):
    """Persistente Button-Leiste unter der Abmeldungsliste."""

    def __init__(self, cog: "AbmeldungCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Abmelden",
        emoji="📝",
        style=discord.ButtonStyle.success,
        custom_id="pulse_abmeldung:abmelden",
    )
    async def abmelden_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Diese Funktion ist nur auf dem Server verfügbar.", ephemeral=True
            )
            return

        if self.cog.is_abgemeldet(interaction.user.id):
            await interaction.response.send_message(
                "ℹ️ Du bist bereits abgemeldet. Nutze **✅ Zurückmelden**, "
                "wenn du wieder zurück bist.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AbmeldungModal(self.cog))

    @discord.ui.button(
        label="Zurückmelden",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="pulse_abmeldung:anmelden",
    )
    async def anmelden_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Diese Funktion ist nur auf dem Server verfügbar.", ephemeral=True
            )
            return

        await self.cog.abmeldung_entfernen_fuer_user(interaction)


class AbmeldungCog(commands.Cog):
    """Team-Abmeldungen mit Discord-Buttons, Datenbank und Live-Liste."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.init_db()
        # Hintergrund-Task für abgelaufene Abmeldungen starten.
        self.check_expired_abmeldungen.start()

    def cog_unload(self):
        self.check_expired_abmeldungen.cancel()

    # ------------------------------------------------------------------
    # Datenbank
    # ------------------------------------------------------------------
    def init_db(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS abmeldungen (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT NOT NULL,
                grund TEXT NOT NULL,
                von TEXT NOT NULL,
                bis TEXT NOT NULL,
                original_nick TEXT,
                guild_id INTEGER
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
            """
        )

        # Automatische Migrationen für ältere Datenbanken.
        migrations = (
            "ALTER TABLE abmeldungen ADD COLUMN original_nick TEXT",
            "ALTER TABLE abmeldungen ADD COLUMN guild_id INTEGER",
            "ALTER TABLE abmeldungen ADD COLUMN von TEXT DEFAULT ''",
        )
        for migration in migrations:
            try:
                cursor.execute(migration)
            except sqlite3.OperationalError:
                pass

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_abmeldungen_bis ON abmeldungen(bis)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_abmeldungen_guild ON abmeldungen(guild_id)"
        )

        conn.commit()
        conn.close()

    def set_config(self, key: str, value: int):
        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def get_config(self, key: str) -> Optional[int]:
        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _get_abmeldung(self, user_id: int):
        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, user_name, grund, von, bis, original_nick, guild_id
                FROM abmeldungen
                WHERE user_id = ?
                """,
                (user_id,),
            )
            return cursor.fetchone()
        finally:
            conn.close()

    def is_abgemeldet(self, user_id: int) -> bool:
        return self._get_abmeldung(user_id) is not None

    @staticmethod
    def parse_date(value: str):
        value = value.strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise ValueError

    @staticmethod
    def display_date(value: str) -> str:
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            try:
                return datetime.strptime(value, "%d.%m.%Y").strftime("%d.%m.%Y")
            except ValueError:
                return value

    # ------------------------------------------------------------------
    # Synchronisation mit Teamliste / Discord
    # ------------------------------------------------------------------
    async def trigger_teamlist_update(self, guild: Optional[discord.Guild]):
        if not guild:
            return

        teamliste_cog = self.bot.get_cog("TeamlisteCog")
        if teamliste_cog and hasattr(teamliste_cog, "update_teamlist"):
            try:
                await teamliste_cog.update_teamlist(guild)
            except Exception as exc:
                print(
                    "⚠️ Fehler beim automatischen Aktualisieren der Teamliste:"
                    f" {exc}"
                )

    async def apply_user_status(self, user_id: int, original_nick: Optional[str], guild_id: int):
        """Stellt sicher, dass die Abmeldungsrolle und der Nickname gesetzt sind."""
        role_id = self.get_config("abgemeldet_role_id")
        guild = self.bot.get_guild(guild_id) if guild_id else None

        if not guild:
            for candidate in self.bot.guilds:
                if candidate.get_member(user_id):
                    guild = candidate
                    break

        if not guild:
            return

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                return

        if role_id:
            role = guild.get_role(role_id)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Pulse: Abmeldung")
                except discord.Forbidden:
                    print(f"⚠️ Keine Rechte, um Abmeldungsrolle an {member} zu vergeben.")
                except discord.HTTPException as exc:
                    print(f"⚠️ Abmeldungsrolle konnte nicht vergeben werden: {exc}")

        # Nur setzen, wenn ein alter Nickname gespeichert ist und der aktuelle
        # Nickname noch nicht als abgemeldet markiert ist.
        if member.nick is None or " | Abgemeldet" not in member.display_name:
            base_name = member.display_name
            new_nick = f"{base_name} | Abgemeldet"[:32]
            try:
                await member.edit(nick=new_nick, reason="Pulse: Abmeldung")
            except discord.Forbidden:
                print(f"⚠️ Keine Rechte, um den Namen von {member} zu ändern.")
            except discord.HTTPException as exc:
                print(f"⚠️ Namensänderung fehlgeschlagen: {exc}")

    async def reset_user_status(
        self, user_id: int, original_nick: Optional[str], guild_id: int
    ):
        """Entfernt Abmeldungsrolle und stellt den vorherigen Nickname wieder her."""
        role_id = self.get_config("abgemeldet_role_id")
        guild = self.bot.get_guild(guild_id) if guild_id else None

        if not guild:
            for candidate in self.bot.guilds:
                if candidate.get_member(user_id):
                    guild = candidate
                    break

        if not guild:
            return

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                return

        if role_id:
            role = guild.get_role(role_id)
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Pulse: Abmeldung beendet")
                except discord.Forbidden:
                    print(f"⚠️ Keine Rechte, um Abmeldungsrolle von {member} zu entfernen.")
                except discord.HTTPException as exc:
                    print(f"⚠️ Abmeldungsrolle konnte nicht entfernt werden: {exc}")

        try:
            await member.edit(nick=original_nick, reason="Pulse: Abmeldung beendet")
        except discord.Forbidden:
            print(f"⚠️ Keine Rechte, um den Namen von {member} zu ändern.")
        except discord.HTTPException as exc:
            print(f"⚠️ Namensänderung fehlgeschlagen: {exc}")

    async def sync_existing_abmeldungen(self):
        """Stellt nach einem Bot-Neustart die Abmeldungsrollen wieder her."""
        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, original_nick, guild_id, bis FROM abmeldungen"
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        today = datetime.now().date()
        for user_id, original_nick, guild_id, bis_str in rows:
            try:
                bis_date = self.parse_date(bis_str).date()
            except ValueError:
                continue
            if today <= bis_date:
                await self.apply_user_status(user_id, original_nick, guild_id or 0)

    # ------------------------------------------------------------------
    # Hintergrund-Task
    # ------------------------------------------------------------------
    @tasks.loop(minutes=15)
    async def check_expired_abmeldungen(self):
        await self.bot.wait_until_ready()

        # Bei jedem Start/Intervall zuerst sicherstellen, dass Liste + Rollen
        # dem Datenbestand entsprechen.
        await self.sync_existing_abmeldungen()

        today = datetime.now().date()
        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, original_nick, guild_id, bis FROM abmeldungen"
            )
            rows = cursor.fetchall()

            expired_users = []
            for user_id, original_nick, guild_id, bis_str in rows:
                try:
                    bis_date = self.parse_date(bis_str).date()
                except ValueError:
                    continue
                if today > bis_date:
                    expired_users.append((user_id, original_nick, guild_id))

            for user_id, _, _ in expired_users:
                cursor.execute(
                    "DELETE FROM abmeldungen WHERE user_id = ?", (user_id,)
                )
            conn.commit()
        finally:
            conn.close()

        for user_id, original_nick, guild_id in expired_users:
            await self.reset_user_status(user_id, original_nick, guild_id or 0)

        # Liste immer synchronisieren – auch wenn sich nur bestehende Einträge
        # geändert haben oder der Bot gerade neu gestartet wurde.
        await self.update_live_list()

        if expired_users:
            print(
                f"🧹 {len(expired_users)} abgelaufene Abmeldung(en) automatisch entfernt."
            )
            guild_ids = {g_id for _, _, g_id in expired_users if g_id}
            for guild_id in guild_ids:
                guild = self.bot.get_guild(guild_id)
                await self.trigger_teamlist_update(guild)

    # ------------------------------------------------------------------
    # Live-Liste
    # ------------------------------------------------------------------
    def _build_embeds(self, rows):
        """Erstellt eine oder mehrere Embeds, damit auch große Teamlisten funktionieren."""
        today = datetime.now().date()
        active = 0
        upcoming = 0

        for _, _, _, von, bis in rows:
            try:
                von_date = self.parse_date(von).date()
                bis_date = self.parse_date(bis).date()
            except ValueError:
                continue
            if today < von_date:
                upcoming += 1
            elif today <= bis_date:
                active += 1

        chunks = [rows[i : i + 25] for i in range(0, len(rows), 25)] or [[]]
        embeds = []

        for index, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title="📌 AKTUELLE ABMELDUNGEN"
                + (f" · Seite {index}/{len(chunks)}" if len(chunks) > 1 else ""),
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )

            if index == 1:
                embed.description = (
                    f"**{len(rows)}** eingetragen · "
                    f"🟢 **{active}** aktiv · ⏳ **{upcoming}** bevorstehend\n"
                    "───────────────\n"
                    "Nutze die Buttons unten, um dich ab- oder zurückzumelden."
                )

            if not chunk:
                embed.description = (
                    ">>> *Aktuell liegen keine Abmeldungen vor.*\n\n"
                    "Drücke unten auf **📝 Abmelden**, um dich einzutragen."
                )
            else:
                for user_id, user_name, grund, von, bis in chunk:
                    status = "🟢 Aktiv"
                    try:
                        von_date = self.parse_date(von).date()
                        if today < von_date:
                            status = "⏳ Beginnt bald"
                    except ValueError:
                        pass

                    embed.add_field(
                        name=f"👤 {user_name} · {status}",
                        value=(
                            f"┣ 📝 **Grund:** {grund}\n"
                            f"┣ 📅 **Von:** {self.display_date(von)}\n"
                            f"┣ 📅 **Bis:** {self.display_date(bis)}\n"
                            f"┗ 👤 <@{user_id}>"
                        ),
                        inline=False,
                    )

            embed.set_footer(
                text="Pulse · Abmeldungssystem • Zuletzt aktualisiert"
            )
            embeds.append(embed)

        return embeds

    async def update_live_list(self):
        channel_id = self.get_config("list_channel_id")
        message_id = self.get_config("list_message_id")

        if not channel_id or not message_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, user_name, grund, von, bis
                FROM abmeldungen
                ORDER BY von ASC, user_name COLLATE NOCASE ASC
                """
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        embeds = self._build_embeds(rows)

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embeds=embeds, view=AbmeldungView(self))
        except discord.NotFound:
            print("⚠️ Abmeldungsliste wurde nicht gefunden. Bitte /setup_liste erneut ausführen.")
        except discord.Forbidden:
            print("⚠️ Keine Rechte, um die Abmeldungsliste zu bearbeiten.")
        except discord.HTTPException as exc:
            print(f"❌ Fehler beim Aktualisieren der Abmeldungsliste: {exc}")

    # ------------------------------------------------------------------
    # Abmeldung intern
    # ------------------------------------------------------------------
    async def abmeldung_eintragen(
        self,
        interaction: discord.Interaction,
        grund: str,
        von: str,
        bis: str,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Abmeldungen können nur auf dem Server eingetragen werden.",
                ephemeral=True,
            )
            return

        # Datenbank + Discord-Synchronisation können mehrere API-Aufrufe enthalten.
        # Früh defer'en verhindert Interaction-Timeouts bei langsamem Hosting.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        try:
            datum_von_obj = self.parse_date(von)
            datum_bis_obj = self.parse_date(bis)
        except ValueError:
            await interaction.followup.send(
                "❌ **Ungültiges Datumsformat!** Bitte nutze `TT.MM.JJJJ`, "
                "z. B. `20.09.2026`.",
                ephemeral=True,
            )
            return

        if datum_bis_obj < datum_von_obj:
            await interaction.followup.send(
                "❌ **Ungültiger Zeitraum!** Das Enddatum darf nicht vor dem "
                "Startdatum liegen.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        current = self._get_abmeldung(user_id)

        # Ein bestehender Eintrag wird aktualisiert; der echte Original-Nick
        # aus dem ersten Eintrag bleibt erhalten.
        if current:
            original_nick = current[5]
        else:
            original_nick = interaction.user.nick

        base_name = interaction.user.display_name.replace(" | Abgemeldet", "").strip()
        von_formatted = datum_von_obj.strftime("%Y-%m-%d")
        bis_formatted = datum_bis_obj.strftime("%Y-%m-%d")

        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO abmeldungen
                    (user_id, user_name, grund, von, bis, original_nick, guild_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    base_name,
                    grund,
                    von_formatted,
                    bis_formatted,
                    original_nick,
                    interaction.guild_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        await self.apply_user_status(user_id, original_nick, interaction.guild_id or 0)
        await self.update_live_list()
        await self.trigger_teamlist_update(interaction.guild)

        await interaction.followup.send(
            "✅ Deine Abmeldung wurde **erfolgreich eingetragen**.\n"
            f"📅 **{von_formatted} → {bis_formatted}**\n"
            f"📝 **{grund}**\n\n"
            "Du erscheinst jetzt automatisch in der Abmeldungsliste.",
            ephemeral=True,
        )

    async def abmeldung_entfernen_fuer_user(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        row = self._get_abmeldung(user_id)

        if not row:
            await interaction.response.send_message(
                "ℹ️ Du bist aktuell nicht abgemeldet.", ephemeral=True
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        _, _, _, _, _, original_nick, guild_id = row

        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

        await self.reset_user_status(
            user_id, original_nick, guild_id or interaction.guild_id or 0
        )
        await self.update_live_list()
        await self.trigger_teamlist_update(interaction.guild)

        await interaction.followup.send(
            "👋 **Willkommen zurück!** Deine Abmeldung wurde entfernt und "
            "die Teamliste wurde aktualisiert.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @app_commands.command(
        name="setup_liste",
        description="[Admin] Erstellt die Abmeldungsliste und setzt optional die Rolle",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        rolle="[Optional] Rolle, die abgemeldeten Mitgliedern gegeben wird"
    )
    async def setup_liste(
        self, interaction: discord.Interaction, rolle: discord.Role = None
    ):
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "❌ Der Befehl kann nur in einem Serverkanal verwendet werden.",
                ephemeral=True,
            )
            return

        self.set_config("list_channel_id", interaction.channel_id)
        if rolle:
            self.set_config("abgemeldet_role_id", rolle.id)

        existing_message_id = self.get_config("list_message_id")
        existing_message = None
        if existing_message_id:
            try:
                existing_message = await interaction.channel.fetch_message(existing_message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                existing_message = None

        await interaction.response.send_message(
            "✅ Abmeldungsliste wird eingerichtet bzw. aktualisiert...",
            ephemeral=True,
        )

        if existing_message:
            self.set_config("list_message_id", existing_message.id)
        else:
            embeds = self._build_embeds([])
            message = await interaction.channel.send(
                embeds=embeds,
                view=AbmeldungView(self),
            )
            self.set_config("list_message_id", message.id)

        await self.update_live_list()
        await self.sync_existing_abmeldungen()
        await self.update_live_list()

        role_info = f" Rolle: {rolle.mention}." if rolle else ""
        await interaction.followup.send(
            "✅ **Abmeldungsliste ist fertig.**\n"
            "Die Buttons **📝 Abmelden** und **✅ Zurückmelden** sind unten aktiv."
            f"{role_info}\n"
            "Vorhandene Abmeldungen aus der Datenbank wurden synchronisiert.",
            ephemeral=True,
        )

    @app_commands.command(
        name="abmeldung",
        description="Melde dich für einen bestimmten Zeitraum ab",
    )
    @app_commands.describe(
        grund="Warum bist du abgemeldet?",
        von="Startdatum Format: TT.MM.JJJJ (z.B. 20.09.2026)",
        bis="Enddatum Format: TT.MM.JJJJ (z.B. 25.09.2026)",
    )
    async def abmeldung(
        self, interaction: discord.Interaction, grund: str, von: str, bis: str
    ):
        await self.abmeldung_eintragen(interaction, grund, von, bis)

    @app_commands.command(name="anmeldung", description="Melde dich wieder zurück")
    async def anmeldung(self, interaction: discord.Interaction):
        await self.abmeldung_entfernen_fuer_user(interaction)

    @app_commands.command(
        name="abmeldung_entfernen",
        description="[Admin] Entferne die Abmeldung eines Mitglieds",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        mitglied="Das Mitglied, dessen Abmeldung entfernt werden soll"
    )
    async def abmeldung_entfernen(
        self, interaction: discord.Interaction, mitglied: discord.Member
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        row = self._get_abmeldung(mitglied.id)
        if not row:
            await interaction.followup.send(
                f"ℹ️ {mitglied.display_name} ist aktuell nicht abgemeldet.",
                ephemeral=True,
            )
            return

        _, _, _, _, _, original_nick, guild_id = row

        conn = sqlite3.connect(DB_NAME)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM abmeldungen WHERE user_id = ?", (mitglied.id,)
            )
            conn.commit()
        finally:
            conn.close()

        await self.reset_user_status(
            mitglied.id, original_nick, guild_id or interaction.guild_id or 0
        )
        await self.update_live_list()
        await self.trigger_teamlist_update(interaction.guild)

        await interaction.followup.send(
            f"✅ Die Abmeldung von {mitglied.mention} wurde entfernt.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    cog = AbmeldungCog(bot)
    # Wichtig: Persistent View überlebt Neustarts des Bots.
    bot.add_view(AbmeldungView(cog))
    await bot.add_cog(cog)
