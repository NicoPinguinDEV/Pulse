import sqlite3
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "abmeldungen.db"

class AbmeldungCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.init_db()
        # Hintergrund-Task für abgelaufene Abmeldungen starten
        self.check_expired_abmeldungen.start()

    def cog_unload(self):
        self.check_expired_abmeldungen.cancel()

    # --- DATENBANK SETUP ---
    def init_db(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS abmeldungen (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT NOT NULL,
                grund TEXT NOT NULL,
                bis TEXT NOT NULL,
                original_nick TEXT,
                guild_id INTEGER
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        """)
        
        # Automatische Datenbank-Migrationen (falls die Datei bereits existiert)
        try:
            cursor.execute("ALTER TABLE abmeldungen ADD COLUMN original_nick TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE abmeldungen ADD COLUMN guild_id INTEGER")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()

    def set_config(self, key: str, value: int):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def get_config(self, key: str):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    # --- HELFER-FUNKTION: ROLLE & NAMEN ZURÜCKSETZEN ---
    async def reset_user_status(self, user_id: int, original_nick: str, guild_id: int):
        role_id = self.get_config("abgemeldet_role_id")
        guild = self.bot.get_guild(guild_id) if guild_id else None

        if not guild:
            for g in self.bot.guilds:
                member = g.get_member(user_id)
                if member:
                    guild = g
                    break

        if guild:
            member = guild.get_member(user_id)
            if not member:
                try:
                    member = await guild.fetch_member(user_id)
                except Exception:
                    member = None

            if member:
                # Rolle entfernen
                if role_id:
                    role = guild.get_role(role_id)
                    if role and role in member.roles:
                        try:
                            await member.remove_roles(role)
                        except discord.Forbidden:
                            print(f"⚠️ Keine Rechte, um Rolle von {member.display_name} zu entfernen.")

                # Namen zurücksetzen
                try:
                    await member.edit(nick=original_nick)
                except discord.Forbidden:
                    print(f"⚠️ Keine Rechte, um den Namen von {member.display_name} zu ändern.")

    # --- HINTERGRUND-TASK: ABGELAUFENE ABMELDUNGEN AUTOMATISCH LÖSCHEN ---
    @tasks.loop(minutes=15)
    async def check_expired_abmeldungen(self):
        await self.bot.wait_until_ready()
        today = datetime.now().date()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, user_name, grund, bis, original_nick, guild_id FROM abmeldungen")
        rows = cursor.fetchall()

        expired_users = []
        for user_id, user_name, grund, bis_str, original_nick, guild_id in rows:
            try:
                bis_date = datetime.strptime(bis_str, "%d.%m.%Y").date()
                # Löschen, wenn das Enddatum überschritten ist
                if today > bis_date:
                    expired_users.append((user_id, original_nick, guild_id))
            except ValueError:
                continue

        for user_id, original_nick, guild_id in expired_users:
            cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (user_id,))
            conn.commit()
            await self.reset_user_status(user_id, original_nick, guild_id)

        conn.close()

        if expired_users:
            print(f"🧹 {len(expired_users)} abgelaufene Abmeldung(en) automatisch entfernt.")
            await self.update_live_list()

    # --- LIVE-LISTE AKTUALISIEREN ---
    async def update_live_list(self):
        channel_id = self.get_config("list_channel_id")
        message_id = self.get_config("list_message_id")

        if not channel_id or not message_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, user_name, grund, bis FROM abmeldungen")
        rows = cursor.fetchall()
        conn.close()

        embed = discord.Embed(
            title="📌 AKTUELLE ABMELDUNGEN",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )

        if not rows:
            embed.description = ">>> *Aktuell liegen keine Abmeldungen vor.*"
        else:
            embed.description = f"Anzahl der Abmeldungen: **{len(rows)}**\n───────────────"
            for user_id, user_name, grund, bis in rows:
                embed.add_field(
                    name=f"👤 {user_name}",
                    value=(
                        f"┣ 📝 **Grund:** {grund}\n"
                        f"┗ 📅 **Bis:** {bis}\n"
                    ),
                    inline=False
                )

        embed.set_footer(text="Zuletzt aktualisiert")

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except Exception as e:
            print(f"❌ Fehler beim Aktualisieren der Liste: {e}")

    # --- COMMANDS ---

    # 1. Admin-Setup (Kanal & Optionale Abmeldungs-Rolle festlegen)
    @app_commands.command(name="setup_liste", description="[Admin] Erstellt die Abmeldungsliste und setzt optional die Rolle")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        rolle="[Optional] Die Rolle, die abgemeldeten Mitgliedern gegeben werden soll"
    )
    async def setup_liste(self, interaction: discord.Interaction, rolle: discord.Role = None):
        embed = discord.Embed(
            title="📌 AKTUELLE ABMELDUNGEN",
            description=">>> *Aktuell liegen keine Abmeldungen vor.*",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Zuletzt aktualisiert")
        
        await interaction.response.send_message("Liste wird erstellt...", ephemeral=True)
        msg = await interaction.channel.send(embed=embed)
        
        self.set_config("list_channel_id", interaction.channel_id)
        self.set_config("list_message_id", msg.id)
        
        if rolle:
            self.set_config("abgemeldet_role_id", rolle.id)
            await interaction.followup.send(f"✅ Liste erstellt & Rolle {rolle.mention} als Abmeldungsrolle gespeichert!", ephemeral=True)
        else:
            await interaction.followup.send("✅ Liste erfolgreich im Kanal erstellt!", ephemeral=True)
            
        await self.update_live_list()

    # 2. /abmeldung
    @app_commands.command(name="abmeldung", description="Melde dich für einen bestimmten Zeitraum ab")
    @app_commands.describe(
        grund="Warum bist du abgemeldet?",
        bis="Format: TT.MM.JJJJ (z.B. 25.09.2026)"
    )
    async def abmeldung(self, interaction: discord.Interaction, grund: str, bis: str):
        try:
            datum_obj = datetime.strptime(bis, "%d.%m.%Y")
            bis_formatted = datum_obj.strftime("%d.%m.%Y")
        except ValueError:
            await interaction.response.send_message(
                "❌ **Ungültiges Datumsformat!** Bitte benutze genau das Format `TT.MM.JJJJ` (z. B. `25.09.2026`).",
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        base_name = interaction.user.display_name

        # Falls der Name schon " | Abgemeldet" enthält, bereinigen
        if " | Abgemeldet" in base_name:
            base_name = base_name.replace(" | Abgemeldet", "").strip()

        original_nick = interaction.user.nick  # Speichert den alten Spitznamen (oder None)

        # 1. Namen auf Discord ändern (Maximal 32 Zeichen erlaubt)
        new_nick = f"{base_name} | Abgemeldet"
        if len(new_nick) > 32:
            new_nick = f"{base_name[:18]}... | Abgemeldet"

        try:
            await interaction.user.edit(nick=new_nick)
        except discord.Forbidden:
            print(f"⚠️ Bot konnte den Namen von {interaction.user.name} nicht ändern (fehlende Rechte / Owner).")

        # 2. Rolle vergeben (falls konfiguriert)
        role_id = self.get_config("abgemeldet_role_id")
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                except discord.Forbidden:
                    print("⚠️ Bot konnte die Rolle nicht vergeben (Rolle liegt in der Hierarchie über der Bot-Rolle).")

        # 3. In Datenbank speichern
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, bis, original_nick, guild_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, base_name, grund, bis_formatted, original_nick, interaction.guild_id))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"✅ Deine Abmeldung bis zum **{bis_formatted}** wurde eingetragen.", ephemeral=True)
        await self.update_live_list()

    # 3. /anmeldung
    @app_commands.command(name="anmeldung", description="Melde dich wieder zurück")
    async def anmeldung(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT original_nick, guild_id FROM abmeldungen WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if row:
            original_nick, guild_id = row[0], row[1]
            cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()

            # Rolle entfernen & Namen zurücksetzen
            await self.reset_user_status(user_id, original_nick, guild_id or interaction.guild_id)

            await interaction.response.send_message("👋 Willkommen zurück! Du wurdest aus der Liste entfernt und dein Name wurde zurückgesetzt.", ephemeral=True)
            await self.update_live_list()
        else:
            conn.close()
            await interaction.response.send_message("Du warst gar nicht abgemeldet!", ephemeral=True)

    # 4. /abmeldung_entfernen (Admin Command)
    @app_commands.command(name="abmeldung_entfernen", description="[Admin] Entferne die Abmeldung eines Mitglieds")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(mitglied="Das Mitglied, dessen Abmeldung gelöscht werden soll")
    async def abmeldung_entfernen(self, interaction: discord.Interaction, mitglied: discord.Member):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT original_nick, guild_id FROM abmeldungen WHERE user_id = ?", (mitglied.id,))
        row = cursor.fetchone()

        if row:
            original_nick, guild_id = row[0], row[1]
            cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (mitglied.id,))
            conn.commit()
            conn.close()

            await self.reset_user_status(mitglied.id, original_nick, guild_id or interaction.guild_id)

            await interaction.response.send_message(f"Die Abmeldung von {mitglied.mention} wurde entfernt.", ephemeral=True)
            await self.update_live_list()
        else:
            conn.close()
            await interaction.response.send_message(f"{mitglied.display_name} war nicht abgemeldet.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AbmeldungCog(bot))
