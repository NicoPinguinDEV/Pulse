import asyncio
import random
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "flaggenquiz.db"

# --- FLAGGEN DATENBANK ---
FLAGS = [
    {
        "name": "Deutschland",
        "answers": ["deutschland"],
        "url": "https://flagcdn.com/w640/de.png",
    },
    {
        "name": "Frankreich",
        "answers": ["frankreich"],
        "url": "https://flagcdn.com/w640/fr.png",
    },
    {
        "name": "Italien",
        "answers": ["italien"],
        "url": "https://flagcdn.com/w640/it.png",
    },
    {
        "name": "Spanien",
        "answers": ["spanien"],
        "url": "https://flagcdn.com/w640/es.png",
    },
    {
        "name": "Japan",
        "answers": ["japan"],
        "url": "https://flagcdn.com/w640/jp.png",
    },
    {
        "name": "Kanada",
        "answers": ["kanada"],
        "url": "https://flagcdn.com/w640/ca.png",
    },
    {
        "name": "Brasilien",
        "answers": ["brasilien"],
        "url": "https://flagcdn.com/w640/br.png",
    },
    {
        "name": "USA",
        "answers": [
            "usa",
            "vereinigte staaten",
            "vereinigte staaten von amerika",
        ],
        "url": "https://flagcdn.com/w640/us.png",
    },
    {
        "name": "Türkei",
        "answers": ["türkei", "turkei"],
        "url": "https://flagcdn.com/w640/tr.png",
    },
    {
        "name": "Ägypten",
        "answers": ["ägypten", "agypten"],
        "url": "https://flagcdn.com/w640/eg.png",
    },
    {
        "name": "Griechenland",
        "answers": ["griechenland"],
        "url": "https://flagcdn.com/w640/gr.png",
    },
    {
        "name": "Schweiz",
        "answers": ["schweiz"],
        "url": "https://flagcdn.com/w640/ch.png",
    },
    {
        "name": "Österreich",
        "answers": ["österreich", "osterreich"],
        "url": "https://flagcdn.com/w640/at.png",
    },
    {
        "name": "Schweden",
        "answers": ["schweden"],
        "url": "https://flagcdn.com/w640/se.png",
    },
    {
        "name": "Norwegen",
        "answers": ["norwegen"],
        "url": "https://flagcdn.com/w640/no.png",
    },
    {
        "name": "Finnland",
        "answers": ["finnland"],
        "url": "https://flagcdn.com/w640/fi.png",
    },
    {
        "name": "Polen",
        "answers": ["polen"],
        "url": "https://flagcdn.com/w640/pl.png",
    },
    {
        "name": "Niederlande",
        "answers": ["niederlande", "holland"],
        "url": "https://flagcdn.com/w640/nl.png",
    },
    {
        "name": "Portugal",
        "answers": ["portugal"],
        "url": "https://flagcdn.com/w640/pt.png",
    },
    {
        "name": "Mexiko",
        "answers": ["mexiko", "mexico"],
        "url": "https://flagcdn.com/w640/mx.png",
    },
    {
        "name": "Argentinien",
        "answers": ["argentinien"],
        "url": "https://flagcdn.com/w640/ar.png",
    },
    {
        "name": "China",
        "answers": ["china"],
        "url": "https://flagcdn.com/w640/cn.png",
    },
    {
        "name": "Südkorea",
        "answers": ["südkorea", "sudkorea", "korea"],
        "url": "https://flagcdn.com/w640/kr.png",
    },
    {
        "name": "Australien",
        "answers": ["australien"],
        "url": "https://flagcdn.com/w640/au.png",
    },
    {
        "name": "Großbritannien",
        "answers": ["großbritannien", "grossbritannien", "england", "uk"],
        "url": "https://flagcdn.com/w640/gb.png",
    },
    {
        "name": "Indien",
        "answers": ["indien"],
        "url": "https://flagcdn.com/w640/in.png",
    },
    {
        "name": "Ukraine",
        "answers": ["ukraine"],
        "url": "https://flagcdn.com/w640/ua.png",
    },
    {
        "name": "Belgien",
        "answers": ["belgien"],
        "url": "https://flagcdn.com/w640/be.png",
    },
    {
        "name": "Dänemark",
        "answers": ["dänemark", "danemark"],
        "url": "https://flagcdn.com/w640/dk.png",
    },
    {
        "name": "Kroatien",
        "answers": ["kroatien"],
        "url": "https://flagcdn.com/w640/hr.png",
    },
]


# --- DATENBANK HELFER ---
def init_db():
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_config (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            current_flag TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_scores (
            guild_id INTEGER,
            user_id INTEGER,
            points INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
  conn.commit()
  conn.close()


def set_quiz_channel(guild_id: int, channel_id: int):
  init_db()
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO quiz_config (guild_id, channel_id) VALUES (?, ?) ON"
      " CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
      (guild_id, channel_id),
  )
  conn.commit()
  conn.close()


def save_current_flag(guild_id: int, flag_name: str):
  init_db()
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "UPDATE quiz_config SET current_flag = ? WHERE guild_id = ?",
      (flag_name, guild_id),
  )
  conn.commit()
  conn.close()


def get_quiz_config(guild_id: int):
  init_db()
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT channel_id, current_flag FROM quiz_config WHERE guild_id = ?",
      (guild_id,),
  )
  row = cursor.fetchone()
  conn.close()
  return row if row else (None, None)


def add_point(guild_id: int, user_id: int):
  init_db()
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO quiz_scores (guild_id, user_id, points)
        VALUES (?, ?, 1)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + 1
    """,
      (guild_id, user_id),
  )
  conn.commit()
  conn.close()


def get_top_scores(guild_id: int, limit: int = 10):
  init_db()
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT user_id, points FROM quiz_scores WHERE guild_id = ? ORDER BY"
      " points DESC LIMIT ?",
      (guild_id, limit),
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


# --- BUTTON VIEW (Skip & Hinweis) ---
class QuizView(discord.ui.View):

  def __init__(self, cog):
    super().__init__(timeout=None)
    self.cog = cog

  @discord.ui.button(
      label="Erster Buchstabe",
      style=discord.ButtonStyle.blurple,
      custom_id="quiz_hint",
      emoji="💡",
  )
  async def hint_button(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    current_flag = self.cog.active_games.get(interaction.channel_id)

    # Nach Neustart: Flagge aus der DB nachladen
    if not current_flag and interaction.guild:
      _, flag_name = get_quiz_config(interaction.guild.id)
      if flag_name:
        current_flag = next((f for f in FLAGS if f["name"] == flag_name), None)
        if current_flag:
          self.cog.active_games[interaction.channel_id] = current_flag

    if not current_flag:
      await interaction.response.send_message(
          "Keine aktive Runde! Nutze `/setup_flaggenquiz`.", ephemeral=True
      )
      return

    first_letter = current_flag["name"][0].upper()
    await interaction.response.send_message(
        f"💡 Der erste Buchstabe des Landes ist: **{first_letter}**",
        ephemeral=True,
    )

  @discord.ui.button(
      label="Überspringen",
      style=discord.ButtonStyle.red,
      custom_id="quiz_skip",
      emoji="⏭️",
  )
  async def skip_button(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    # Verhindert den 3-Sekunden-Timeout!
    await interaction.response.defer()

    current_flag = self.cog.active_games.get(interaction.channel_id)

    if not current_flag and interaction.guild:
      _, flag_name = get_quiz_config(interaction.guild.id)
      if flag_name:
        current_flag = next((f for f in FLAGS if f["name"] == flag_name), None)

    if not current_flag:
      await interaction.followup.send(
          "Keine aktive Runde! Nutze `/setup_flaggenquiz`.", ephemeral=True
      )
      return

    skipped_country_name = current_flag["name"]
    await interaction.followup.send(
        f"⏭️ {interaction.user.mention} hat die Flagge übersprungen! Gesucht war:"
        f" **{skipped_country_name}**"
    )
    await asyncio.sleep(1.0)
    await self.cog.start_new_round(
        interaction.channel, skipped_country=skipped_country_name
    )


class FlaggenQuizCog(commands.Cog):

  def __init__(self, bot: commands.Bot):
    self.bot = bot
    init_db()
    self.active_games = {}  # {channel_id: flag_object}

  async def cog_load(self):
    # Macht die Buttons dauerhaft auf Discord verfügbar
    self.bot.add_view(QuizView(self))

  @commands.Cog.listener()
  async def on_ready(self):
    # Nach Neustart aktive Spiele aus der DB wiederherstellen
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id, channel_id, current_flag FROM quiz_config")
    rows = cursor.fetchall()
    conn.close()

    for g_id, ch_id, flag_name in rows:
      if ch_id and flag_name:
        flag_obj = next((f for f in FLAGS if f["name"] == flag_name), None)
        if flag_obj:
          self.active_games[ch_id] = flag_obj

  def create_quiz_embed(
      self,
      guild: discord.Guild,
      flag_data: dict,
      last_winner: discord.Member = None,
      last_country: str = None,
      skipped_country: str = None,
  ) -> discord.Embed:
    top_scores = get_top_scores(guild.id, limit=10)

    leaderboard_lines = ["🏆 **TOP 10 BESTENLISTE**"]
    if not top_scores:
      leaderboard_lines.append("*Bisher hat noch niemand eine Flagge erraten!*")
    else:
      for idx, (u_id, pts) in enumerate(top_scores, start=1):
        member = guild.get_member(u_id)
        member_name = member.display_name if member else f"User ID: {u_id}"
        medal = (
            "🥇"
            if idx == 1
            else "🥈" if idx == 2 else "🥉" if idx == 3 else f"`#{idx}`"
        )
        leaderboard_lines.append(f"{medal} **{member_name}** — `{pts}` Punkte")

    leaderboard_text = "\n".join(leaderboard_lines)

    description_text = (
        f"{leaderboard_text}\n\n"
        f"───────────────────────────────\n\n"
        f"🎮 **AKTUELLES RÄTSEL**\n"
        f"Welches Land gehört zu dieser Flagge?\n"
        f"Schreibe den **vollständigen Namen** einfach in den Chat!"
    )

    embed = discord.Embed(
        title=f"🚩 Flaggen-Raten | {guild.name.upper()}",
        description=description_text,
        color=discord.Color.red(),
    )
    embed.set_image(url=flag_data["url"])

    if skipped_country:
      embed.set_footer(
          text=f"⏭️ Die letzte Flagge ({skipped_country}) wurde übersprungen."
      )
    elif last_winner and last_country:
      embed.set_footer(
          text=(
              f"⚡ Letzte Flagge ({last_country}) richtig erraten von:"
              f" {last_winner.display_name}"
          )
      )
    else:
      embed.set_footer(text="Viel Erfolg beim Erraten! ⚡")

    return embed

  async def start_new_round(
      self,
      channel: discord.TextChannel,
      last_winner: discord.Member = None,
      last_country: str = None,
      skipped_country: str = None,
  ):
    new_flag = random.choice(FLAGS)
    self.active_games[channel.id] = new_flag
    save_current_flag(channel.guild.id, new_flag["name"])

    try:
      await channel.purge(limit=100)
    except Exception:
      pass

    embed = self.create_quiz_embed(
        channel.guild, new_flag, last_winner, last_country, skipped_country
    )
    view = QuizView(self)
    await channel.send(embed=embed, view=view)

  @commands.Cog.listener()
  async def on_message(self, message: discord.Message):
    if message.author.bot or not message.guild:
      return

    quiz_channel_id, current_flag_name = get_quiz_config(message.guild.id)
    if not quiz_channel_id or message.channel.id != quiz_channel_id:
      return

    current_flag = self.active_games.get(message.channel.id)

    # Wenn der Bot neugestartet wurde, Aktionsdatenbank abfragen
    if not current_flag and current_flag_name:
      current_flag = next(
          (f for f in FLAGS if f["name"] == current_flag_name), None
      )
      if current_flag:
        self.active_games[message.channel.id] = current_flag

    if not current_flag:
      return

    user_input = message.content.strip().lower()

    if user_input in current_flag["answers"]:
      add_point(message.guild.id, message.author.id)
      last_country_name = current_flag["name"]
      winner = message.author

      await asyncio.sleep(0.5)
      await self.start_new_round(
          message.channel, last_winner=winner, last_country=last_country_name
      )

  @app_commands.command(
      name="setup_flaggenquiz",
      description="[Admin] Richtet den Kanal für das Flaggen-Raten ein",
  )
  @app_commands.checks.has_permissions(administrator=True)
  async def setup_quiz(
      self, interaction: discord.Interaction, kanal: discord.TextChannel
  ):
    set_quiz_channel(interaction.guild.id, kanal.id)

    await interaction.response.send_message(
        f"✅ Flaggen-Quiz wurde im Kanal {kanal.mention} eingerichtet!",
        ephemeral=True,
    )
    await self.start_new_round(kanal)


async def setup(bot: commands.Bot):
  await bot.add_cog(FlaggenQuizCog(bot))
