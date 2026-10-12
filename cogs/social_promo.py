"""Automated social video announcements for Pulse."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, urlparse

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

TARGET_CHANNEL_ID = 1530972281893949521
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = Path(os.getenv("PULSE_SOCIAL_PROMO_FILE", str(ROOT / "social_promo_sources.json")))
RSSHUB_BASE_URL = os.getenv("PULSE_RSSHUB_BASE_URL", "https://rsshub.app").strip().rstrip("/")
MAX_SOURCES = 50
MAX_SEEN_IDS = 80
MAX_POSTS_PER_CYCLE = 5
POLL_INTERVAL_MINUTES = 2
USER_AGENT = "PulseSocialPromo/1.0 (+Discord community automation)"


def _clean_text(value: str, limit: int = 500) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = " ".join(value.split())
    return discord.utils.escape_mentions(value)[:limit]


def _is_public_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost", ".test")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False
        except ValueError:
            pass
        return True
    except (TypeError, ValueError):
        return False


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, names: set[str]) -> str:
    for child in list(element):
        if _tag_name(child.tag) in names:
            if child.text:
                return child.text.strip()
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return ""


def _parse_entry_date(raw: str) -> float:
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def parse_feed(xml_text: str) -> tuple[str, list[dict[str, str]]]:
    """Parse RSS 2.0 or Atom without a third-party dependency."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("Die Quelle liefert kein gültiges RSS-/Atom-XML.") from exc

    feed_title = _child_text(root, {"title"})
    if not feed_title:
        for child in list(root):
            if _tag_name(child.tag) == "channel":
                feed_title = _child_text(child, {"title"})
                break

    items: list[dict[str, str]] = []
    for node in root.iter():
        if _tag_name(node.tag) not in {"item", "entry"}:
            continue
        title = _child_text(node, {"title"})
        link = ""
        for child in list(node):
            if _tag_name(child.tag) == "link":
                rel = child.attrib.get("rel", "alternate")
                candidate = child.attrib.get("href") or child.text or ""
                if candidate.strip() and rel in {"alternate", ""}:
                    link = candidate.strip()
                    break
        if not link:
            link = _child_text(node, {"link"})
        item_id = _child_text(node, {"guid", "id"}) or link
        published = _child_text(node, {"published", "pubdate", "updated", "date"})
        description = _child_text(node, {"description", "summary", "content", "encoded"})
        author = _child_text(node, {"author", "creator", "name"})
        if title and link.startswith(("https://", "http://")) and item_id:
            items.append({
                "id": item_id[:500],
                "title": _clean_text(title, 240) or "Neuer Beitrag",
                "url": link[:1800],
                "published": published[:100],
                "description": _clean_text(description, 600),
                "author": _clean_text(author, 120),
            })
    unique: list[dict[str, str]] = []
    found: set[str] = set()
    for item in items:
        if item["id"] in found:
            continue
        found.add(item["id"])
        unique.append(item)
    return feed_title, unique[:100]


def _name_from_url(platform: str, value: str) -> str:
    parsed = urlparse(value if "://" in value else "https://" + value.lstrip("@"))
    path_parts = [p for p in parsed.path.split("/") if p]
    if platform in {"youtube", "tiktok", "instagram", "twitch", "x"} and path_parts:
        return path_parts[0].lstrip("@")
    return value.strip().lstrip("@")[:100]


def _youtube_channel_id_from_input(value: str) -> str | None:
    raw = value.strip()
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", raw):
        return raw
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    if parsed.hostname and parsed.hostname.lower().endswith("youtube.com"):
        match = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})", parsed.path)
        if match:
            return match.group(1)
        channel_id = parse_qs(parsed.query).get("channel_id", [])
        if channel_id and re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel_id[0]):
            return channel_id[0]
    return None


class SocialPromo(commands.Cog):
    """Poll social/RSS feeds and announce newly published content in Discord."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sources = self._load_sources()
        self._poll_lock = asyncio.Lock()
        self.poll_sources.start()

    def cog_unload(self):
        self.poll_sources.cancel()

    def _load_sources(self) -> dict[str, dict]:
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sources"), list):
                sources = {}
                for item in data["sources"]:
                    if isinstance(item, dict) and item.get("id") and item.get("feed_url"):
                        sources[str(item["id"])] = item
                return sources
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _save_sources(self) -> None:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
        temp.write_text(
            json.dumps({"version": 1, "sources": list(self.sources.values())}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(DATA_FILE)

    async def _get_text(self, url: str) -> str:
        if not _is_public_https_url(url):
            raise ValueError("Nur öffentliche HTTPS-URLs sind erlaubt.")
        timeout = httpx.Timeout(15.0, connect=7.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(url)
            if not _is_public_https_url(str(response.url)):
                raise ValueError("Die Quelle leitete auf eine nicht erlaubte Adresse weiter.")
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError("Die Feed-Antwort ist zu groß (Limit: 2 MB).")
            return response.text

    async def _resolve_youtube_handle(self, value: str) -> str:
        raw = value.strip()
        if "://" in raw:
            parsed = urlparse(raw)
        elif re.match(r"^(www\\.)?youtube\\.com/", raw, re.IGNORECASE):
            parsed = urlparse("https://" + raw)
        elif raw.startswith("@"):
            parsed = urlparse("https://www.youtube.com/" + raw)
        else:
            parsed = urlparse("https://www.youtube.com/" + raw.lstrip("/"))
        if not parsed.hostname or not parsed.hostname.lower().endswith("youtube.com"):
            raise ValueError("Bitte eine YouTube-Kanal-URL, @Handle oder Kanal-ID angeben.")
        channel_id = _youtube_channel_id_from_input(raw)
        if channel_id:
            return channel_id
        page_url = "https://www.youtube.com" + parsed.path if parsed.path else "https://www.youtube.com/" + raw.lstrip("/")
        page = await self._get_text(page_url)
        patterns = (
            r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
            r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
            r'/channel/(UC[A-Za-z0-9_-]{22})',
        )
        for pattern in patterns:
            match = re.search(pattern, page)
            if match:
                return match.group(1)
        raise ValueError("YouTube-Kanal-ID konnte nicht automatisch ermittelt werden. Nutze die Kanal-ID oder einen offiziellen YouTube-RSS-Feed.")

    def _build_feed_url(self, platform: str, source: str) -> tuple[str, str]:
        raw = source.strip()
        if platform == "rss":
            if not _is_public_https_url(raw):
                raise ValueError("Bitte eine öffentliche HTTPS-Adresse zum RSS-/Atom-Feed angeben.")
            return raw, rawparse_name(raw)

        if platform == "youtube":
            if "://" in raw:
                parsed = urlparse(raw)
            elif re.match(r"^(www\\.)?youtube\\.com/", raw, re.IGNORECASE):
                parsed = urlparse("https://" + raw)
            else:
                parsed = urlparse("https://www.youtube.com/" + raw.lstrip("/"))
            if parsed.path.endswith("videos.xml") and parsed.hostname and parsed.hostname.lower().endswith("youtube.com"):
                url = raw if "://" in raw else "https://" + raw
                if _is_public_https_url(url):
                    return url, "YouTube"
            channel_id = _youtube_channel_id_from_input(raw)
            if channel_id:
                return "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id, _name_from_url(platform, raw)
            return "RESOLVE_YOUTUBE:" + raw, _name_from_url(platform, raw)

        if platform not in {"tiktok", "instagram", "twitch", "x"}:
            raise ValueError("Diese Plattform wird nicht unterstützt.")
        handle = _name_from_url(platform, raw)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", handle):
            raise ValueError("Der Benutzername enthält ungültige Zeichen.")
        quoted = quote(handle, safe="._-")
        if platform == "tiktok":
            feed = RSSHUB_BASE_URL + "/tiktok/user/%40" + quoted
        elif platform == "instagram":
            feed = RSSHUB_BASE_URL + "/instagram/2/user/" + quoted
        elif platform == "twitch":
            feed = RSSHUB_BASE_URL + "/twitch/video/" + quoted + "/archive"
        else:
            feed = RSSHUB_BASE_URL + "/twitter/user/" + quoted
        if not _is_public_https_url(feed):
            raise ValueError("Die konfigurierte RSSHub-Adresse muss eine öffentliche HTTPS-Adresse sein.")
        return feed, handle

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def poll_sources(self):
        if self._poll_lock.locked():
            return
        async with self._poll_lock:
            for source_id in list(self.sources.keys()):
                source = self.sources.get(source_id)
                if not source or not source.get("enabled", True):
                    continue
                try:
                    await self._poll_one(source_id, source)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print("⚠️ Social-Promo: Quelle %s fehlgeschlagen (%s)." % (source.get("name", source_id), type(exc).__name__))

    @poll_sources.before_loop
    async def before_poll_sources(self):
        await self.bot.wait_until_ready()

    async def _poll_one(self, source_id: str, source: dict) -> None:
        feed_url = str(source.get("feed_url", ""))
        xml_text = await self._get_text(feed_url)
        feed_title, items = parse_feed(xml_text)
        if not items:
            source["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save_sources()
            return

        current_ids = [item["id"] for item in items]
        stored_seen = set(str(x) for x in source.get("seen_ids", []))
        if not source.get("initialized"):
            source["seen_ids"] = current_ids[:MAX_SEEN_IDS]
            source["initialized"] = True
            source["last_item"] = current_ids[0]
            source["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if feed_title and not source.get("custom_name"):
                source["feed_title"] = _clean_text(feed_title, 100)
            self._save_sources()
            return

        new_items = [item for item in items if item["id"] not in stored_seen]
        if new_items:
            new_items = sorted(new_items, key=lambda item: _parse_entry_date(item.get("published", "")))
            new_items = new_items[-MAX_POSTS_PER_CYCLE:]
            channel = self.bot.get_channel(TARGET_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(TARGET_CHANNEL_ID)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    print("⚠️ Social-Promo: Zielkanal %s nicht erreichbar (%s)." % (TARGET_CHANNEL_ID, type(exc).__name__))
                    return
            if not hasattr(channel, "send"):
                print("⚠️ Social-Promo: Ziel-ID ist kein beschreibbarer Discord-Kanal.")
                return
            display_name = _clean_text(source.get("name") or source.get("feed_title") or feed_title or "Social-Kanal", 100)
            for item in new_items:
                embed = discord.Embed(
                    title=("🎬 " + item["title"])[:256],
                    url=item["url"],
                    description="**Neuer Beitrag von " + display_name + "!**\nJetzt ansehen und unterstützen. 👇",
                    color=discord.Color.from_rgb(239, 68, 68) if source.get("platform") in {"youtube", "tiktok"} else discord.Color.blurple(),
                    timestamp=datetime.now(timezone.utc),
                )
                if item.get("description"):
                    embed.add_field(name="Info", value=item["description"][:1024], inline=False)
                embed.set_footer(text="Pulse Social Promo • " + str(source.get("platform_label") or source.get("platform", "Social")).upper())
                try:
                    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print("⚠️ Social-Promo: Nachricht für %s konnte nicht gesendet werden (%s)." % (display_name, type(exc).__name__))
                    return
                await asyncio.sleep(0.5)

        merged = current_ids + [x for x in source.get("seen_ids", []) if x not in current_ids]
        source["seen_ids"] = merged[:MAX_SEEN_IDS]
        source["last_item"] = current_ids[0]
        source["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if feed_title and not source.get("custom_name"):
            source["feed_title"] = _clean_text(feed_title, 100)
        self._save_sources()

    @app_commands.command(name="social_add", description="Automatische Video-/Social-Ankündigung hinzufügen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(plattform=[
        app_commands.Choice(name="YouTube", value="youtube"),
        app_commands.Choice(name="TikTok", value="tiktok"),
        app_commands.Choice(name="Instagram", value="instagram"),
        app_commands.Choice(name="Twitch-Videos", value="twitch"),
        app_commands.Choice(name="X / Twitter", value="x"),
        app_commands.Choice(name="RSS-/Atom-Feed (beliebige Plattform)", value="rss"),
    ])
    async def social_add(
        self,
        interaction: discord.Interaction,
        plattform: app_commands.Choice[str],
        quelle: str,
        name: str | None = None,
    ):
        if len(self.sources) >= MAX_SOURCES:
            await interaction.response.send_message("Maximal %d Social-Quellen sind erlaubt." % MAX_SOURCES, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        platform = plattform.value
        try:
            feed_url, inferred_name = self._build_feed_url(platform, quelle)
            if feed_url.startswith("RESOLVE_YOUTUBE:"):
                channel_id = await self._resolve_youtube_handle(feed_url.split(":", 1)[1])
                feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id
            if not _is_public_https_url(feed_url):
                raise ValueError("Die erzeugte Feed-Adresse ist keine erlaubte öffentliche HTTPS-Adresse.")
            xml_text = await self._get_text(feed_url)
            feed_title, items = parse_feed(xml_text)
            if not feed_title and not items:
                raise ValueError("Die Adresse liefert keinen erkennbaren RSS-/Atom-Feed.")
        except (ValueError, httpx.HTTPError, ET.ParseError) as exc:
            msg = str(exc)[:850] if isinstance(exc, ValueError) else "Die Quelle konnte nicht abgerufen werden. Prüfe den Link, die Erreichbarkeit und mögliche Plattform-Sperren."
            await interaction.followup.send("❌ Quelle nicht hinzugefügt: " + msg, ephemeral=True)
            return
        duplicate = next((s for s in self.sources.values() if s.get("feed_url") == feed_url), None)
        if duplicate:
            await interaction.followup.send("Diese Quelle ist bereits eingetragen (ID: %s)." % duplicate["id"], ephemeral=True)
            return
        source_id = uuid.uuid4().hex[:8].upper()
        label = {
            "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
            "twitch": "Twitch", "x": "X / Twitter", "rss": "RSS/Atom",
        }.get(platform, platform)
        source = {
            "id": source_id,
            "platform": platform,
            "platform_label": label,
            "name": _clean_text((name or inferred_name or feed_title or label).strip(), 100),
            "custom_name": bool(name and name.strip()),
            "feed_title": _clean_text(feed_title, 100),
            "feed_url": feed_url,
            "added_by": str(interaction.user.id),
            "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "enabled": True,
            "initialized": True,
            "seen_ids": [item["id"] for item in items][:MAX_SEEN_IDS],
            "last_item": items[0]["id"] if items else "",
            "last_checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.sources[source_id] = source
        try:
            self._save_sources()
        except OSError:
            self.sources.pop(source_id, None)
            await interaction.followup.send("❌ Die Quelle wurde geprüft, konnte aber nicht dauerhaft gespeichert werden. Prüfe Schreibrechte im Hosting.", ephemeral=True)
            return
        await interaction.followup.send(
            "✅ **Social-Quelle hinzugefügt**\n"
            + "**Plattform:** " + label + "\n"
            + "**Quelle:** " + source["name"] + "\n"
            + "**ID:** " + source_id + "\n"
            + "**Feed:** " + feed_url + "\n\n"
            + "Ab jetzt werden neue Einträge automatisch in <#" + str(TARGET_CHANNEL_ID) + "> beworben. Bereits veröffentlichte Einträge werden nicht rückwirkend gepostet.",
            ephemeral=True,
        )

    @app_commands.command(name="social_list", description="Alle überwachten Social-Videoquellen anzeigen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def social_list(self, interaction: discord.Interaction):
        if not self.sources:
            await interaction.response.send_message("Es sind noch keine Social-Quellen eingetragen. Nutze /social_add.", ephemeral=True)
            return
        lines = []
        for source in list(self.sources.values())[:40]:
            status = "🟢 aktiv" if source.get("enabled", True) else "⏸️ pausiert"
            lines.append(
                source.get("id", "?") + " · " + status + " · **"
                + _clean_text(source.get("name", "Unbenannt"), 100) + "** ("
                + str(source.get("platform_label", source.get("platform", "Social"))) + ")"
            )
        embed = discord.Embed(title="📡 Social-Promo · Quellen", description="\n".join(lines), color=discord.Color.blurple())
        embed.add_field(name="Ankündigungskanal", value="<#" + str(TARGET_CHANNEL_ID) + ">", inline=False)
        embed.set_footer(text="Verwaltung: /social_add · /social_remove · /social_toggle · /social_test")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="social_remove", description="Eine überwachte Social-Quelle entfernen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def social_remove(self, interaction: discord.Interaction, quelle_id: str):
        key = quelle_id.strip().upper()
        source = self.sources.get(key)
        if not source:
            await interaction.response.send_message("Diese Quellen-ID wurde nicht gefunden. Nutze /social_list.", ephemeral=True)
            return
        removed = self.sources.pop(key)
        try:
            self._save_sources()
        except OSError:
            self.sources[removed["id"]] = removed
            await interaction.response.send_message("Die Quelle konnte nicht gespeichert entfernt werden. Prüfe die Schreibrechte.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Social-Quelle %s (%s) entfernt." % (removed.get("name", "Unbenannt"), removed["id"]), ephemeral=True)

    @app_commands.command(name="social_toggle", description="Eine Social-Quelle pausieren oder fortsetzen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def social_toggle(self, interaction: discord.Interaction, quelle_id: str, aktiv: bool):
        source = self.sources.get(quelle_id.strip().upper())
        if not source:
            await interaction.response.send_message("Diese Quellen-ID wurde nicht gefunden. Nutze /social_list.", ephemeral=True)
            return
        source["enabled"] = aktiv
        self._save_sources()
        await interaction.response.send_message(
            ("✅ Quelle wieder aktiviert: " if aktiv else "⏸️ Quelle pausiert: ")
            + source.get("name", "Unbenannt") + " (" + source["id"] + ").",
            ephemeral=True,
        )

    @app_commands.command(name="social_test", description="Testnachricht im Social-Promo-Kanal senden")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def social_test(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = self.bot.get_channel(TARGET_CHANNEL_ID) or await self.bot.fetch_channel(TARGET_CHANNEL_ID)
            embed = discord.Embed(
                title="✅ Social-Promo-Test",
                description="Pulse kann den Ankündigungskanal erreichen. Neue Videos werden hier automatisch beworben.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError) as exc:
            await interaction.followup.send(
                "❌ Test fehlgeschlagen (%s). Prüfe, ob die Kanal-ID stimmt und Pulse dort Nachrichten und Embeds senden darf." % type(exc).__name__,
                ephemeral=True,
            )
            return
        await interaction.followup.send("✅ Testnachricht wurde in <#%d> gesendet." % TARGET_CHANNEL_ID, ephemeral=True)


def rawparse_name(value: str) -> str:
    parsed = urlparse(value)
    return (parsed.hostname or "RSS-Feed").replace("www.", "")[:100]


async def setup(bot: commands.Bot):
    await bot.add_cog(SocialPromo(bot))
