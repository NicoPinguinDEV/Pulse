"""Pure helpers for creator video announcements (no Discord/network dependency)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# label, embed colour, emoji, verb for the announcement
PLATFORM_META = {
    "youtube": ("YouTube", 0xFF0033, "▶️", "ein neues Video"),
    "tiktok": ("TikTok", 0x25F4EE, "🎵", "ein neues TikTok"),
    "instagram": ("Instagram", 0xE1306C, "📸", "ein neues Reel"),
    "twitch": ("Twitch", 0x9146FF, "🟣", "ein neues Video"),
    "x": ("X / Twitter", 0x111111, "✖️", "einen neuen Video-Post"),
    "rss": ("Feed", 0x5865F2, "📡", "einen neuen Beitrag"),
}

MAX_ANNOUNCE_AGE_HOURS = 72


def platform_meta(platform: str) -> tuple[str, int, str, str]:
    return PLATFORM_META.get(str(platform), PLATFORM_META["rss"])


def detect_platform(link: str) -> tuple[str, str]:
    """Return (platform, cleaned input) for a pasted profile/channel link or @handle.

    Raises ValueError with a helpful German message for links that cannot be a profile.
    """
    raw = str(link or "").strip()
    if not raw:
        raise ValueError("Bitte einen Link zum Kanal oder Profil angeben.")
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", raw):
        return "youtube", raw
    candidate = raw if "://" in raw else "https://" + raw.lstrip("/")
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    host = host[2:] if host.startswith("m.") else host
    if host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        if host == "youtu.be" or parsed.path.startswith(("/watch", "/shorts/")):
            raise ValueError(
                "Das ist ein Video-Link. Bitte den Link zum YouTube-Kanal einfügen "
                "(z. B. https://www.youtube.com/@kanalname)."
            )
        return "youtube", candidate
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok", candidate
    if host in {"instagram.com", "instagr.am"}:
        return "instagram", candidate
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return "twitch", candidate
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return "x", candidate
    if raw.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_.-]{1,100}", raw):
        # A bare @handle is most commonly a YouTube handle.
        return "youtube", raw
    if parsed.scheme == "https" and host:
        return "rss", candidate
    raise ValueError("Dieser Link wurde nicht erkannt. Unterstützt: YouTube, TikTok, Instagram, Twitch, X oder ein RSS-Feed.")


def host_is(host: str | None, domain: str) -> bool:
    """*host* is *domain* itself or one of its subdomains (evil-youtube.com is not youtube.com)."""
    host = str(host or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    if host_is(host, "youtu.be"):
        vid = parsed.path.strip("/").split("/")[0]
    elif host_is(host, "youtube.com"):
        if parsed.path.startswith("/shorts/"):
            vid = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
        else:
            vid = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        return None
    return vid if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or "") else None


def is_short(url: str) -> bool:
    return "/shorts/" in str(url or "")


def best_thumbnail(item: dict, platform: str) -> str:
    """Pick a large preview image: YouTube's own thumbnail, else the feed's image."""
    if platform == "youtube":
        vid = youtube_video_id(item.get("url", ""))
        if vid:
            return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    thumb = str(item.get("thumbnail") or "")
    if thumb.startswith("https://"):
        return thumb[:1000]
    match = re.search(r'<img[^>]+src=["\'](https://[^"\']+)["\']', str(item.get("raw_description") or ""), re.IGNORECASE)
    return match.group(1)[:1000] if match else ""


def profile_url(platform: str, handle: str, feed_url: str = "") -> str:
    handle = str(handle or "").lstrip("@")
    if platform == "youtube":
        match = re.search(r"channel_id=(UC[A-Za-z0-9_-]{22})", feed_url or "")
        if match:
            return f"https://www.youtube.com/channel/{match.group(1)}"
        return f"https://www.youtube.com/@{handle}" if handle else "https://www.youtube.com"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "twitch":
        return f"https://www.twitch.tv/{handle}"
    if platform == "x":
        return f"https://x.com/{handle}"
    return ""


def is_recent(published_ts: float, now_ts: float, max_hours: int = MAX_ANNOUNCE_AGE_HOURS) -> bool:
    """Unknown dates count as recent; dated items older than the window are not announced."""
    if not published_ts:
        return True
    return now_ts - published_ts <= max_hours * 3600


def announcement_text(name: str, platform: str, url: str, creator_mention: str = "") -> str:
    label, _, emoji, noun = platform_meta(platform)
    if platform == "youtube" and is_short(url):
        noun = "einen neuen Short"
    who = f"**{name}**" + (f" ({creator_mention})" if creator_mention else "")
    return f"{emoji} {who} hat {noun} auf **{label}** hochgeladen!"
