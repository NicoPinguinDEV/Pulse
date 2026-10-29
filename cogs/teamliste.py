import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import discord
from pulse_embed_design import color as _pulse_color
from discord import app_commands
from discord.ext import commands, tasks

import pulse_jobs

BASE_DIR = Path(__file__).resolve().parents[1]
DB_NAME = str(BASE_DIR / "teamliste.db")
ABMELDUNGEN_DB = str(BASE_DIR / "abmeldungen.db")


def _embed_signature(embed: discord.Embed) -> tuple:
    """Compare all user-visible embed fields before sending a Discord PATCH."""
    colour = embed.color.value if embed.color else None
    timestamp = embed.timestamp.isoformat() if embed.timestamp else None
    fields = tuple((field.name, field.value, bool(field.inline)) for field in embed.fields)
    footer = (embed.footer.text, embed.footer.icon_url)
    author = (embed.author.name, embed.author.url, embed.author.icon_url)
    image = (embed.image.url, embed.thumbnail.url)
    return (embed.title, embed.description, embed.url, colour, timestamp, footer, author, image, fields)


def _retry_after_seconds(exc: discord.HTTPException) -> float:
    """Parse Discord Retry-After; unknown headers fall back to a conservative pause."""
    value = getattr(exc, "retry_after", None)
    if value is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return max(0.0, min(3600.0, float(value)))
    except (TypeError, ValueError):
        return 5.0



# --- DATENBANK HELFER ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_id INTEGER UNIQUE,
            category TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            key TEXT PRIMARY KEY,
            msg_id INTEGER
        )
    """)
    conn.commit()
    conn.close()


def set_config(key: str, value: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
    conn.close()


def get_config(key: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# Rollen-Verwaltung in DB
def add_role_to_db(role_id: int, category: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO roles (role_id, category) VALUES (?, ?)",
        (role_id, category),
    )
    conn.commit()
    conn.close()


def remove_role_from_db(role_id: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM roles WHERE role_id = ?", (role_id,))
    conn.commit()
    conn.close()


def clear_category_from_db(category: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM roles WHERE category = ?", (category,))
    conn.commit()
    conn.close()


def get_roles_by_category(category: str) -> list[int]:
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role_id FROM roles WHERE category = ? ORDER BY id ASC",
        (category,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


# Nachricht-IDs speichern
def set_msg_id(key: str, msg_id: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO messages (key, msg_id) VALUES (?, ?)",
        (key, msg_id),
    )
    conn.commit()
    conn.close()


def get_msg_id(key: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT msg_id FROM messages WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


class TeamlisteCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        self._is_updating = False  # Verhindert parallele Updates
        self._update_debounce_task: asyncio.Task | None = None
        self.update_list_loop.start()

    def cog_unload(self):
        self.update_list_loop.cancel()
        if self._update_debounce_task and not self._update_debounce_task.done():
            self._update_debounce_task.cancel()

    # Liest Abmeldungen einmal pro Listenaktualisierung statt pro Teammitglied.
    def get_abmeldungen_map(self) -> dict[int, str]:
        try:
            with closing(sqlite3.connect(ABMELDUNGEN_DB, timeout=3)) as conn, conn:
                rows = conn.execute("SELECT user_id, bis FROM abmeldungen").fetchall()
            return {int(user_id): str(bis) for user_id, bis in rows if user_id is not None}
        except (sqlite3.Error, OSError, TypeError, ValueError):
            return {}

    # Reads the shared shift record once per team-list refresh. An active shift
    # takes precedence for display, while the leave remains recorded separately.
    def get_active_shift_map(self) -> dict[int, str]:
        try:
            with open(BASE_DIR / "shifts.json", "r", encoding="utf-8") as file:
                data = json.load(file)
            active = data.get("active_shifts", {}) if isinstance(data, dict) else {}
            if not isinstance(active, dict):
                return {}
            result = {}
            for user_id, shift in active.items():
                if not str(user_id).isdigit() or not isinstance(shift, dict):
                    continue
                status = str(shift.get("status") or "online").lower()
                if status in {"online", "break"}:
                    result[int(user_id)] = status
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    # Legacy-Helfer für andere Aufrufer.
    def get_abmeldung_info(self, user_id: int) -> str | None:
        return self.get_abmeldungen_map().get(int(user_id))

    # Formatiert den Online-Status mit Status-Punkten
    def format_status(self, status: discord.Status) -> str:
        if status == discord.Status.online:
            return "🟢 Online"
        elif status == discord.Status.idle:
            return "🟡 Abwesend"
        elif status == discord.Status.dnd:
            return "🔴 Bitte nicht stören"
        else:
            return "⚪ Offline"

    # Erstellt das Embed im SCNX-Stil mit rotem Rand
    def create_team_embed(
        self, guild: discord.Guild, title: str, category: str
    ) -> discord.Embed:
        role_ids = get_roles_by_category(category)
        lines = []

        if not role_ids:
            lines.append(
                f"*Keine Rollen für {title} konfiguriert. Nutze `/teamrollen_hinzufuegen`.*"
            )
        else:
            # 1. Rollen-Objekte laden
            roles = [guild.get_role(rid) for rid in role_ids]
            # 2. Ungültige/gelöschte Rollen herausfiltern
            valid_roles = [r for r in roles if r is not None]
            # 3. Nach der Server-Hierarchie (Position) absteigend sortieren
            sorted_roles = sorted(valid_roles, key=lambda r: r.position, reverse=True)

            # Eine DB-Abfrage statt einer SQLite-Verbindung je Mitglied.
            abmeldungen = self.get_abmeldungen_map()
            active_shifts = self.get_active_shift_map()
            for role in sorted_roles:
                lines.append(f"❯ **{role.name}**")

                if role.members:
                    for member in role.members:
                        bis_datum = abmeldungen.get(member.id)
                        shift_status = active_shifts.get(member.id)
                        if shift_status == "online":
                            status_text = (
                                f"🟢 Im Dienst (Abmeldung bis {bis_datum} hinterlegt)"
                                if bis_datum else "🟢 Im Dienst"
                            )
                        elif shift_status == "break":
                            status_text = (
                                f"☕ Pause (Abmeldung bis {bis_datum} hinterlegt)"
                                if bis_datum else "☕ Pause"
                            )
                        elif bis_datum:
                            status_text = f"🌴 Abgemeldet (bis {bis_datum})"
                        else:
                            status_text = self.format_status(member.status)
                        lines.append(f"• {member.mention}:  {status_text}")
                else:
                    lines.append(
                        f"Kein Mitglied des Servers hat die {role.mention} Rolle."
                    )

                lines.append("")

        description_text = "\n".join(lines)

        # Rot gefärbtes Embed
        embed = discord.Embed(
            title=f"{title} | {guild.name.upper()}",
            description=description_text[:4000],
            color=_pulse_color("danger"),
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.set_footer(text="Powered by TeamBot ⚡")
        return embed

    # Aktualisiert die 3 Embed-Nachrichten
    async def update_teamlist(self, guild: discord.Guild):
        if self._is_updating:
            return

        self._is_updating = True
        try:
            channel_id = get_config("teamlist_channel_id")
            if not channel_id:
                return

            channel = guild.get_channel(channel_id)
            if not channel:
                return

            categories = [
                ("fuehrungsebene", "Führungsebenen-Liste"),
                ("highteam", "HighTeam-Liste"),
                ("lowteam", "LowTeam-Liste"),
            ]

            for key, title in categories:
                embed = self.create_team_embed(guild, title, key)
                msg_id = get_msg_id(key)

                if msg_id and msg_id != 0:
                    msg = None
                    try:
                        msg = await channel.fetch_message(msg_id)
                        if len(msg.embeds) == 1 and _embed_signature(msg.embeds[0]) == _embed_signature(embed):
                            continue

                        await msg.edit(embed=embed)
                        await asyncio.sleep(2)

                    except discord.NotFound:
                        new_msg = await channel.send(embed=embed)
                        set_msg_id(key, new_msg.id)
                        await asyncio.sleep(2)

                    except discord.HTTPException as e:
                        if e.status == 429:
                            retry_after = _retry_after_seconds(e)
                            if retry_after > 30:
                                print(f"⏳ Teamliste ({key}): Rate-Limit aktiv; nächster regulärer Durchlauf übernimmt (Retry-After {retry_after:.1f}s).")
                                return
                            await asyncio.sleep(retry_after)
                            try:
                                if msg is None:
                                    msg = await channel.fetch_message(msg_id)
                                await msg.edit(embed=embed)
                                await asyncio.sleep(2)
                            except discord.NotFound:
                                new_msg = await channel.send(embed=embed)
                                set_msg_id(key, new_msg.id)
                                await asyncio.sleep(2)
                            except discord.HTTPException as retry_error:
                                print(f"⏳ Teamliste ({key}): erneuter Discord-Aufruf fehlgeschlagen; Durchlauf wird beendet ({retry_error.status}).")
                                return
                        elif e.code == 30046:
                            try:
                                await channel.purge(limit=1, check=lambda m: m.id == msg_id)
                            except discord.HTTPException:
                                pass
                            new_msg = await channel.send(embed=embed)
                            set_msg_id(key, new_msg.id)
                            await asyncio.sleep(2)
                        else:
                            print(f"HTTP-Fehler beim Aktualisieren der Teamliste ({key}): {e}")
                else:
                    new_msg = await channel.send(embed=embed)
                    set_msg_id(key, new_msg.id)
                    await asyncio.sleep(2)

        finally:
            self._is_updating = False

    # Sicherheitsabgleich alle fünf Minuten; Änderungen werden separat gebündelt.
    @tasks.loop(minutes=5)
    @pulse_jobs.tracked("Teamliste Abgleich (5 min)", 300, pausable=False)  # P-06: visible in the system status
    async def update_list_loop(self):
        await self.bot.wait_until_ready()
        channel_id = get_config("teamlist_channel_id")
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel and channel.guild:
                # Ein Fehler (z. B. Forbidden beim Neuanlegen) darf den 5-Minuten-Abgleich nicht dauerhaft beenden.
                try:
                    await self.update_teamlist(channel.guild)
                except Exception as e:
                    print(f"⚠️ Teamliste: Sicherheitsabgleich fehlgeschlagen ({type(e).__name__}); nächster Durchlauf versucht es erneut.")
                    pulse_jobs.record_error("Teamliste Abgleich (5 min)", e)

    async def _debounced_update_teamlist(self, guild: discord.Guild):
        try:
            # Mehrere Rollen-/Nickname-Updates in kurzer Folge zu einem Update bündeln.
            await asyncio.sleep(8)
            if not self.bot.is_closed():
                await self.update_teamlist(guild)
        except asyncio.CancelledError:
            raise
        finally:
            if self._update_debounce_task is asyncio.current_task():
                self._update_debounce_task = None

    # Rollen-/Namensänderungen lösen keine parallelen Listen-Neuberechnungen aus.
    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ):
        if before.roles != after.roles or before.display_name != after.display_name:
            if self._update_debounce_task and not self._update_debounce_task.done():
                self._update_debounce_task.cancel()
            self._update_debounce_task = asyncio.create_task(
                self._debounced_update_teamlist(after.guild),
                name="pulse-teamlist-debounced-update",
            )

    # COMMANDS
    @app_commands.command(
        name="setup_teamliste",
        description="[Admin] Richtet den Kanal für die Teamlisten ein",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_teamliste(
        self, interaction: discord.Interaction, kanal: discord.TextChannel
    ):
        set_config("teamlist_channel_id", kanal.id)
        set_msg_id("fuehrungsebene", 0)
        set_msg_id("highteam", 0)
        set_msg_id("lowteam", 0)

        await interaction.response.send_message(
            f"✅ Teamliste-Kanal auf {kanal.mention} gesetzt! Generiere Embeds...",
            ephemeral=True,
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(
        name="teamrollen_hinzufuegen",
        description="[Admin] Fügt bis zu 10 Rollen gleichzeitig hinzu",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(
        liste=[
            app_commands.Choice(
                name="Führungsebenen-Liste", value="fuehrungsebene"
            ),
            app_commands.Choice(name="HighTeam-Liste", value="highteam"),
            app_commands.Choice(name="LowTeam-Liste", value="lowteam"),
        ]
    )
    async def add_roles(
        self,
        interaction: discord.Interaction,
        liste: app_commands.Choice[str],
        rolle1: discord.Role,
        rolle2: discord.Role = None,
        rolle3: discord.Role = None,
        rolle4: discord.Role = None,
        rolle5: discord.Role = None,
        rolle6: discord.Role = None,
        rolle7: discord.Role = None,
        rolle8: discord.Role = None,
        rolle9: discord.Role = None,
        rolle10: discord.Role = None,
    ):
        input_roles = [
            r
            for r in [
                rolle1,
                rolle2,
                rolle3,
                rolle4,
                rolle5,
                rolle6,
                rolle7,
                rolle8,
                rolle9,
                rolle10,
            ]
            if r is not None
        ]

        added_mentions = []
        for role in input_roles:
            add_role_to_db(role.id, liste.value)
            added_mentions.append(role.mention)

        await interaction.response.send_message(
            f"✅ **{len(added_mentions)} Rolle(n)** zur **{liste.name}** hinzugefügt:\n"
            + ", ".join(added_mentions),
            ephemeral=True,
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(
        name="teamrolle_entfernen",
        description="[Admin] Entfernt eine Rolle aus der Teamliste",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_role(
        self, interaction: discord.Interaction, rolle: discord.Role
    ):
        remove_role_from_db(rolle.id)
        await interaction.response.send_message(
            f"🗑️ Rolle {rolle.mention} wurde entfernt!", ephemeral=True
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(
        name="teamliste_leeren",
        description="[Admin] Löscht alle Rollen aus einer bestimmten Liste",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(
        liste=[
            app_commands.Choice(
                name="Führungsebenen-Liste", value="fuehrungsebene"
            ),
            app_commands.Choice(name="HighTeam-Liste", value="highteam"),
            app_commands.Choice(name="LowTeam-Liste", value="lowteam"),
        ]
    )
    async def clear_roles(
        self, interaction: discord.Interaction, liste: app_commands.Choice[str]
    ):
        clear_category_from_db(liste.value)
        await interaction.response.send_message(
            f"🧹 Alle Rollen aus der **{liste.name}** wurden gelöscht!",
            ephemeral=True,
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(
        name="update_teamliste",
        description="[Admin] Erzwingt eine sofortige Aktualisierung der Teamliste",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def force_update_teamliste(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.update_teamlist(interaction.guild)
        await interaction.followup.send(
            "✅ Teamliste wurde manuell aktualisiert!", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TeamlisteCog(bot))
