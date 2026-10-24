"""Automated social video announcements for Pulse."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import os
import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, urlparse

import discord
from pulse_embed_design import color as _pulse_color
import httpx
from discord import app_commands
from discord.ext import commands, tasks
import pulse_ids as _pulse_ids  # A-09: fixed Discord IDs live in one place

from pulse_social_utils import (
    announcement_text,
    best_thumbnail,
    detect_platform,
    is_recent,
    platform_meta,
    profile_url,
)

TARGET_CHANNEL_ID = _pulse_ids.SOCIAL_TARGET_CHANNEL_ID
SOCIAL_PING_ROLE_ID = _pulse_ids.SOCIAL_PING_ROLE_ID
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = Path(os.getenv("PULSE_SOCIAL_PROMO_FILE", str(ROOT / "social_promo_sources.json")))
RSSHUB_BASE_URL = os.getenv("PULSE_RSSHUB_BASE_URL", "https://rsshub.app").strip().rstrip("/")
# Public RSSHub mirrors listed by the RSSHub project. They are best-effort fallbacks:
# platforms such as Instagram/TikTok may still require credentials or browser support.
_DEFAULT_RSSHUB_FALLBACK_URLS = (
    "https://rsshub.rssforever.com",
    "https://rsshub.feeded.xyz",
    "https://hub.slarker.me",
)
_raw_rsshub_fallbacks = os.getenv("PULSE_RSSHUB_FALLBACK_URLS")
RSSHUB_FALLBACK_BASE_URLS = tuple(
    value.strip().rstrip("/")
    for value in (
        _DEFAULT_RSSHUB_FALLBACK_URLS
        if _raw_rsshub_fallbacks is None
        else _raw_rsshub_fallbacks.split(",")
    )
    if value and value.strip()
)
MAX_SOURCES = 50
MAX_SEEN_IDS = 80
MAX_FEED_BYTES = 2_000_000
MAX_POSTS_PER_CYCLE = 5
POLL_INTERVAL_MINUTES = 2
POLL_FAILURE_INITIAL_BACKOFF_SECONDS = 300
POLL_FAILURE_MAX_BACKOFF_SECONDS = 3600
POLL_FAILURE_LOG_INTERVAL_SECONDS = 3600
USER_AGENT = "PulseSocialPromo/1.0 (+Discord community automation)"


class FeedCooldownError(ValueError):
    """All feed hosts are cooling down; retry only when the earliest cooldown expires."""

    def __init__(self, message: str, retry_after_seconds: float):
        super().__init__(message)
        self.retry_after_seconds = max(0, min(3600, int(retry_after_seconds)))


def _retry_after_seconds(exc: Exception) -> int:
    """Respect a feed server's Retry-After header, including HTTP-date values."""
    explicit = getattr(exc, "retry_after_seconds", 0)
    try:
        seconds = float(explicit or 0)
    except (TypeError, ValueError):
        seconds = 0
    response = getattr(exc, "response", None)
    raw = response.headers.get("retry-after", "").strip() if response is not None else ""
    if raw:
        try:
            seconds = max(seconds, float(raw))
        except (TypeError, ValueError):
            try:
                target = parsedate_to_datetime(raw)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                seconds = max(seconds, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return max(0, min(3600, int(seconds + 0.999)))


def _clean_text(value: str, limit: int = 500) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = " ".join(value.split())
    return discord.utils.escape_mentions(value)[:limit]


def _describe_fetch_error(exc: Exception, platform: str) -> str:
    """Turn feed network failures into actionable, non-sensitive diagnostics."""
    request = getattr(exc, "request", None)
    host = getattr(getattr(request, "url", None), "host", None) or "Feed-Server"

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        request_host = getattr(getattr(exc.response, "url", None), "host", None) or host
        if status in {401, 403}:
            hint = (
                "Der Server verweigert den Abruf. Deine Eingabe muss deshalb nicht falsch sein."
            )
            if platform in {"tiktok", "instagram", "twitch", "x"}:
                hint += (
                    " Bei dieser Plattform kann die öffentliche RSSHub-Instanz durch "
                    "Anti-Bot-Schutz oder fehlende Zugangsdaten blockiert werden. "
                    "Konfiguriere im Hosting PULSE_RSSHUB_BASE_URL mit einer erreichbaren "
                    "RSSHub-Instanz; Instagram-Feeds können eigene Zugangsdaten/Cookies erfordern."
                )
            return f"{hint} (HTTP {status}, {request_host})."
        if status == 429:
            return (
                f"Der Feed-Server begrenzt momentan die Anfragen (HTTP 429, {request_host}). "
                "Warte kurz und versuche es erneut; gegebenenfalls ist eine andere RSSHub-Instanz nötig."
            )
        if status == 404:
            return (
                f"Der Feed-Endpunkt oder das Profil wurde nicht gefunden (HTTP 404, {request_host}). "
                "Prüfe den Profilnamen und ob die Route auf dem Feed-Dienst verfügbar ist."
            )
        if status >= 500:
            return (
                f"Der Feed-Dienst meldet einen Serverfehler (HTTP {status}, {request_host}). "
                "Das ist meist vorübergehend oder liegt am RSS-/Plattformanbieter."
            )
        return f"Der Feed-Server antwortet mit HTTP {status} ({request_host}). Prüfe die Feed-Adresse."

    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Zeitüberschreitung beim Abruf von {host}. Der Feed-Dienst hat nicht rechtzeitig "
            "geantwortet; prüfe die Erreichbarkeit oder versuche es später erneut."
        )
    if isinstance(exc, httpx.RequestError):
        return (
            f"Netzwerkfehler beim Abruf von {host} ({type(exc).__name__}). "
            "Mögliche Ursachen sind DNS, TLS, eine Netzwerkblockade oder ein nicht erreichbarer Feed-Dienst."
        )
    return "Die Quelle konnte nicht abgerufen werden. Prüfe die Feed-Adresse und deren Erreichbarkeit."


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


async def _resolves_to_public(host: str) -> bool:
    """M-A3-1: a DNS name that points to a private/loopback address is as internal as the address itself."""
    try:
        ipaddress.ip_address(host)
        return True  # literal addresses are judged by _is_public_https_url
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return True  # not resolvable: the request itself fails with a normal connection error
    return all(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]).is_global for info in infos)


async def _check_outgoing_request(request: httpx.Request) -> None:
    """httpx request hook: runs for the first request and for every redirect hop, before it is sent."""
    if not _is_public_https_url(str(request.url)) or not await _resolves_to_public(request.url.host):
        raise ValueError("Die Quelle verweist auf eine nicht erlaubte (interne) Adresse.")


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
        if not description:
            # YouTube nests the text in <media:group><media:description>.
            for child in node.iter():
                if child is not node and _tag_name(child.tag) == "description" and child.text:
                    description = child.text.strip()
                    break
        author = _child_text(node, {"author", "creator", "name"})
        # Preview image: media:thumbnail / image enclosure / media:content (YouTube, RSSHub).
        thumbnail = ""
        for child in node.iter():
            tag = _tag_name(child.tag)
            url_attr = (child.attrib.get("url") or "").strip()
            if not url_attr.startswith("https://"):
                continue
            if tag == "thumbnail" or (
                tag in {"enclosure", "content"}
                and (child.attrib.get("type", "").startswith("image/") or child.attrib.get("medium") == "image")
            ):
                thumbnail = url_attr
                break
        if title and link.startswith(("https://", "http://")) and item_id:
            items.append({
                "id": item_id[:500],
                "title": _clean_text(title, 240) or "Neuer Beitrag",
                "url": link[:1800],
                "published": published[:100],
                "description": _clean_text(description, 600),
                "raw_description": html.unescape(description)[:4000],
                "author": _clean_text(author, 120),
                "thumbnail": thumbnail[:1000],
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
        # Track failed mirror hosts across sources. If one public RSSHub instance
        # is down, do not make every configured source retry the same dead host.
        self._host_retry_at: dict[str, float] = {}
        self._host_failure_count: dict[str, int] = {}
        # Keep one bounded connection pool instead of opening sockets on every
        # source and RSSHub fallback attempt.
        self._http_client: httpx.AsyncClient | None = None
        self.poll_sources.start()

    async def cog_unload(self):
        self.poll_sources.cancel()
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

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

    def _record_host_failure(self, host: str, exc: Exception) -> None:
        """Temporarily cool down a mirror that is rate-limiting or unavailable."""
        transient = isinstance(exc, (httpx.TimeoutException, httpx.RequestError))
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            transient = status in (401, 403, 429) or status >= 500
        if not transient:
            return

        failures = min(self._host_failure_count.get(host, 0) + 1, 8)
        self._host_failure_count[host] = failures
        pause = min(
            3600,
            max(
                POLL_FAILURE_INITIAL_BACKOFF_SECONDS * (2 ** min(failures - 1, 4)),
                _retry_after_seconds(exc),
            ),
        )
        self._host_retry_at[host] = time.monotonic() + pause

        # Keep the diagnostic cache bounded even if many one-off feeds are added.
        if len(self._host_retry_at) > 64:
            oldest = sorted(
                self._host_retry_at,
                key=lambda key: self._host_retry_at[key],
            )
            for old_host in oldest[:len(self._host_retry_at) - 64]:
                self._host_retry_at.pop(old_host, None)
                self._host_failure_count.pop(old_host, None)

    def _record_host_success(self, host: str) -> None:
        self._host_retry_at.pop(host, None)
        self._host_failure_count.pop(host, None)

    def _host_cooldown(self, host: str) -> float:
        return max(0.0, self._host_retry_at.get(host, 0.0) - time.monotonic())

    async def _get_text(self, url: str) -> str:
        if not _is_public_https_url(url):
            raise ValueError("Nur öffentliche HTTPS-URLs sind erlaubt.")
        if self._http_client is None or self._http_client.is_closed:
            timeout = httpx.Timeout(15.0, connect=7.0)
            self._http_client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                event_hooks={"request": [_check_outgoing_request]},
                headers={"User-Agent": USER_AGENT},
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=3, keepalive_expiry=20.0),
            )
        # Stream and cap the body before buffering it. Checking response.content
        # after a normal GET still allows an arbitrarily large feed to occupy RAM.
        async with self._http_client.stream("GET", url) as response:
            if not _is_public_https_url(str(response.url)):
                raise ValueError("Die Quelle leitete auf eine nicht erlaubte Adresse weiter.")
            response.raise_for_status()
            content_length = response.headers.get("content-length", "")
            try:
                declared_size = int(content_length)
            except (ValueError, TypeError):
                declared_size = 0
            if declared_size > MAX_FEED_BYTES:
                raise ValueError("Die Feed-Antwort ist zu groß (Limit: 2 MB).")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_FEED_BYTES:
                    raise ValueError("Die Feed-Antwort ist zu groß (Limit: 2 MB).")
                chunks.append(chunk)
            encoding = response.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace")

    def _feed_candidates(self, feed_url: str, platform: str) -> list[str]:
        """Return the original RSSHub URL plus configured public mirrors for social routes."""
        if platform not in {"tiktok", "instagram", "twitch", "x"}:
            return [feed_url]

        parsed_feed = urlparse(feed_url)
        known_bases = [RSSHUB_BASE_URL, *RSSHUB_FALLBACK_BASE_URLS]
        known_hosts = {
            urlparse(base).hostname.lower()
            for base in known_bases
            if base and urlparse(base).hostname
        }
        if not parsed_feed.hostname or parsed_feed.hostname.lower() not in known_hosts:
            # Never rewrite arbitrary user-provided RSS feeds to unrelated third-party hosts.
            return [feed_url]

        candidates: list[str] = [feed_url]
        for base in known_bases:
            if not _is_public_https_url(base):
                continue
            parsed_base = urlparse(base)
            if not parsed_base.netloc:
                continue
            candidate = parsed_feed._replace(scheme="https", netloc=parsed_base.netloc).geturl()
            if candidate not in candidates:
                candidates.append(candidate)

        if platform == "tiktok":
            # RSSHub documents an optional true route flag to use TikTok's official
            # iframe rendering mode. Try the ordinary route first, then this variant
            # across the same instances before marking the source temporarily unavailable.
            for candidate in list(candidates):
                parsed_candidate = urlparse(candidate)
                if not parsed_candidate.path.rstrip("/").endswith("/true"):
                    iframe_candidate = parsed_candidate._replace(
                        path=parsed_candidate.path.rstrip("/") + "/true"
                    ).geturl()
                    if iframe_candidate not in candidates:
                        candidates.append(iframe_candidate)
        return candidates

    async def _fetch_feed(
        self, feed_url: str, platform: str
    ) -> tuple[str, str, list[dict[str, str]]]:
        """Fetch and parse a feed, retrying other known RSSHub instances for social profiles."""
        candidates = self._feed_candidates(feed_url, platform)
        failures: list[tuple[str, Exception]] = []
        skipped_hosts: dict[str, float] = {}
        attempted = 0

        for candidate in candidates:
            host = urlparse(candidate).hostname or "Feed-Server"
            remaining = self._host_cooldown(host)
            if remaining > 0:
                skipped_hosts[host] = max(skipped_hosts.get(host, 0.0), remaining)
                continue
            attempted += 1
            try:
                xml_text = await self._get_text(candidate)
                feed_title, items = parse_feed(xml_text)
                if not feed_title and not items:
                    raise ValueError("Die Adresse liefert keinen erkennbaren RSS-/Atom-Feed.")
                self._record_host_success(host)
                if candidate != feed_url:
                    print(f"✅ Social-Promo: RSSHub-Fallback für {platform} erfolgreich ({host}).")
                return candidate, feed_title, items
            except asyncio.CancelledError:
                raise
            except (ValueError, httpx.HTTPError, ET.ParseError) as exc:
                failures.append((host, exc))
                self._record_host_failure(host, exc)
                if len(candidates) == 1:
                    raise

        if attempted == 0:
            earliest = min(skipped_hosts.values(), default=float(POLL_FAILURE_INITIAL_BACKOFF_SECONDS))
            retry_minutes = max(1, int((earliest + 59) // 60))
            skipped = ", ".join(
                f"{host} ({max(1, int((remaining + 59) // 60))} Min.)"
                for host, remaining in sorted(skipped_hosts.items())
            )
            raise FeedCooldownError(
                "Keine RSSHub-Instanz konnte den Feed abrufen (alle Kandidaten sind "
                f"wegen vorheriger Serverfehler vorübergehend gedrosselt; nächster Probeversuch "
                f"in ca. {retry_minutes} Min.; {skipped}).",
                retry_after_seconds=earliest,
            )

        details: list[str] = []
        for host, exc in failures:
            if isinstance(exc, httpx.HTTPStatusError):
                reason = f"HTTP {exc.response.status_code}"
            elif isinstance(exc, httpx.TimeoutException):
                reason = "Zeitüberschreitung"
            elif isinstance(exc, httpx.RequestError):
                reason = type(exc).__name__
            else:
                reason = str(exc)[:90]
            details.append(f"{host}: {reason}")

        hint = ""
        if platform == "instagram":
            hint = (
                " Instagram-RSSHub-Routen sind stark eingeschränkt; häufig braucht die "
                "verwendete RSSHub-Instanz eigene IG_USERNAME/IG_PASSWORD-Konfiguration "
                "oder geeignete Cookies. Öffentliche Mirrors können deshalb ebenfalls scheitern."
            )
        elif platform == "tiktok":
            hint = (
                " Die TikTok-Route ist durch Anti-Crawler-Schutz empfindlich. Pulse hat auch die "
                "optionale RSSHub-Variante mit offiziellem TikTok-iframe ausprobiert. "
                "Wenn alle Instanzen weiter 403/503 oder Timeouts liefern, ist eine eigene "
                "RSSHub-Instanz oder eine autorisierte TikTok-API-Anbindung erforderlich."
            )
        raise ValueError(
            "Keine RSSHub-Instanz konnte den Feed abrufen (" + "; ".join(details) + ")." + hint
        )

    async def _resolve_youtube_handle(self, value: str) -> str:
        raw = value.strip()
        if "://" in raw:
            parsed = urlparse(raw)
        elif re.match(r"^(www\.)?youtube\.com/", raw, re.IGNORECASE):
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
            elif re.match(r"^(www\.)?youtube\.com/", raw, re.IGNORECASE):
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

        if platform == "tiktok":
            # A common mistake is pasting a TikTok share/short link or display name.
            # RSSHub needs the actual profile handle, not vm.tiktok.com/... or a video ID.
            looks_like_url = (
                "://" in raw
                or raw.lower().startswith(("www.tiktok.com/", "tiktok.com/", "vm.tiktok.com/", "vt.tiktok.com/"))
            )
            if looks_like_url:
                parsed_tiktok = urlparse(raw if "://" in raw else "https://" + raw)
                host = (parsed_tiktok.hostname or "").lower()
                if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
                    raise ValueError(
                        "Bitte eine TikTok-Profiladresse wie https://www.tiktok.com/@benutzername "
                        "oder nur @benutzername angeben."
                    )
                parts = [part for part in parsed_tiktok.path.split("/") if part]
                if host in {"vm.tiktok.com", "vt.tiktok.com"} or not parts or not parts[0].startswith("@"):
                    raise ValueError(
                        "Das sieht nicht wie eine vollständige TikTok-Profiladresse aus. "
                        "Öffne das Profil deines Kollegen und kopiere die Profil-URL mit /@benutzername "
                        "oder gib nur @benutzername ein. Verkürzte Teilen-Links und reine Video-Links "
                        "werden hier nicht als Profil erkannt."
                    )
                handle = parts[0][1:]
            else:
                handle = raw.strip().lstrip("@")
        else:
            handle = _name_from_url(platform, raw)

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", handle):
            raise ValueError("Der Benutzername enthält ungültige Zeichen.")
        quoted = quote(handle, safe="._-")
        if platform == "tiktok":
            # RSSHub's documented route uses a literal @ in the path:
            # /tiktok/user/@username. Keep it unescaped for compatibility with proxies/WAFs.
            feed = RSSHUB_BASE_URL + "/tiktok/user/@" + quoted
        elif platform == "instagram":
            feed = RSSHUB_BASE_URL + "/instagram/2/user/" + quoted
        elif platform == "twitch":
            feed = RSSHUB_BASE_URL + "/twitch/video/" + quoted + "/archive"
        else:
            feed = RSSHUB_BASE_URL + "/twitter/user/" + quoted
        if not _is_public_https_url(feed):
            raise ValueError("Die konfigurierte RSSHub-Adresse muss eine öffentliche HTTPS-Adresse sein.")
        return feed, handle

    @staticmethod
    def _is_video_item(source: dict, item: dict[str, str]) -> bool:
        platform = str(source.get("platform", "rss"))
        if platform in {"youtube", "tiktok", "twitch"}:
            # These feed adapters specifically return videos or recorded broadcasts.
            return True
        url = str(item.get("url", "")).lower()
        raw = str(item.get("raw_description", "")).lower()
        if platform == "instagram":
            # Only Reels/IGTV and explicit video media should be advertised, not photo posts.
            return (
                "/reel/" in url or "/tv/" in url
                or "video_url" in raw or "video.mp4" in raw
                or "video.twimg.com" in raw or "<video" in raw
            )
        if platform == "x":
            # X timelines also contain text-only posts. Require a recognizable video/media marker.
            return (
                "video.twimg.com" in raw or "video.syndication.twimg.com" in raw
                or "ext_tw_video_thumb" in raw or "video_url" in raw or "<video" in raw
                or "youtube.com/watch" in raw or "youtu.be/" in raw
                or "tiktok.com/" in raw or "instagram.com/reel/" in raw
            )
        # A custom RSS/Atom source should be a video-only feed selected by the administrator.
        return True

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def poll_sources(self):
        if self._poll_lock.locked():
            return
        async with self._poll_lock:
            for source_id in list(self.sources.keys()):
                source = self.sources.get(source_id)
                if not source or not source.get("enabled", True):
                    continue
                retry_after = _parse_entry_date(str(source.get("retry_after", "")))
                if retry_after and retry_after > datetime.now(timezone.utc).timestamp():
                    continue
                try:
                    await self._poll_one(source_id, source)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(
                        "⚠️ Social-Promo: Quelle %s fehlgeschlagen (%s: %s)."
                        % (source.get("name", source_id), type(exc).__name__, str(exc)[:250])
                    )

    @poll_sources.before_loop
    async def before_poll_sources(self):
        await self.bot.wait_until_ready()

    async def _target_channel(self):
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(TARGET_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print("⚠️ Social-Promo: Zielkanal %s nicht erreichbar (%s)." % (TARGET_CHANNEL_ID, type(exc).__name__))
                return None
        if not hasattr(channel, "send"):
            print("⚠️ Social-Promo: Ziel-ID ist kein beschreibbarer Discord-Kanal.")
            return None
        return channel

    def build_announcement(self, source: dict, item: dict, feed_title: str = ""):
        """Professional creator announcement: role ping, creator line, big thumbnail, link buttons."""
        platform = str(source.get("platform", "rss"))
        label, color, emoji, _ = platform_meta(platform)
        name = _clean_text(source.get("name") or source.get("feed_title") or feed_title or "Creator", 100)
        creator_id = str(source.get("creator_id") or "")
        creator_mention = f"<@{creator_id}>" if creator_id.isdigit() else ""
        channel_url = profile_url(platform, source.get("source_key") or source.get("name", ""), source.get("feed_url", ""))
        content = f"<@&{SOCIAL_PING_ROLE_ID}>\n" + announcement_text(name, platform, item["url"], creator_mention)

        embed = discord.Embed(
            title=item["title"][:256],
            url=item["url"],
            color=discord.Color(color),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name=f"{name} · {label}"[:256], url=channel_url or None)
        if item.get("description"):
            embed.description = item["description"][:350] + ("…" if len(item["description"]) > 350 else "")
        published = _parse_entry_date(item.get("published", ""))
        embed.add_field(name="📺 Plattform", value=label, inline=True)
        if creator_mention:
            embed.add_field(name="🎥 Creator", value=creator_mention, inline=True)
        if published:
            embed.add_field(name="🕒 Veröffentlicht", value=f"<t:{int(published)}:R>", inline=True)
        thumbnail = best_thumbnail(item, platform)
        if thumbnail:
            embed.set_image(url=thumbnail)
        embed.set_footer(text="Pulse Creator-Ankündigung • Schau vorbei und lass ein Like da! 💙")

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Jetzt ansehen", emoji=emoji, url=item["url"][:512]))
        if channel_url:
            view.add_item(discord.ui.Button(label="Kanal besuchen", emoji="👤", url=channel_url[:512]))
        return content, embed, view

    async def _announce(self, channel, source: dict, item: dict, feed_title: str = "") -> bool:
        content, embed, view = self.build_announcement(source, item, feed_title)
        try:
            await channel.send(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    # Ping only the notification role; the creator mention is shown without a ping.
                    roles=[discord.Object(id=SOCIAL_PING_ROLE_ID)],
                    users=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            print("⚠️ Social-Promo: Ankündigung für %s konnte nicht gesendet werden (%s)." % (source.get("name"), type(exc).__name__))
            return False

    async def announce_latest(self, source_id: str) -> str:
        """Re-announce the newest video of one source (test/manual trigger from the dashboard)."""
        source = self.sources.get(str(source_id).upper())
        if not source:
            raise ValueError("Quelle nicht gefunden.")
        feed_url, feed_title, items = await self._fetch_feed(str(source.get("feed_url", "")), str(source.get("platform", "rss")))
        videos = [item for item in items if self._is_video_item(source, item)]
        if not videos:
            raise ValueError("Im Feed wurde noch kein Video gefunden.")
        latest = max(videos, key=lambda item: _parse_entry_date(item.get("published", "")))
        channel = await self._target_channel()
        if channel is None or not await self._announce(channel, source, latest, feed_title):
            raise ValueError("Der Ankündigungskanal ist nicht erreichbar oder Pulse darf dort nicht schreiben.")
        return latest["title"]

    async def add_source(self, platform: str, quelle: str, name: str | None, creator_id: str | None, added_by: str) -> tuple[dict, bool]:
        """Validate a profile, resolve its feed and store it. Raises ValueError with a German message."""
        if len(self.sources) >= MAX_SOURCES:
            raise ValueError("Maximal %d Social-Quellen sind erlaubt." % MAX_SOURCES)
        try:
            feed_url, inferred_name = self._build_feed_url(platform, quelle)
            if feed_url.startswith("RESOLVE_YOUTUBE:"):
                channel_id = await self._resolve_youtube_handle(feed_url.split(":", 1)[1])
                feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id
            if not _is_public_https_url(feed_url):
                raise ValueError("Die erzeugte Feed-Adresse ist keine erlaubte öffentliche HTTPS-Adresse.")
            pending_feed = False
            pending_feed_error = ""
            try:
                feed_url, feed_title, items = await self._fetch_feed(feed_url, platform)
            except ValueError as exc:
                # Save the validated profile route and let the poll retry a recovering mirror.
                if platform in {"tiktok", "instagram", "twitch", "x"} and str(exc).startswith("Keine RSSHub-Instanz konnte den Feed abrufen"):
                    pending_feed = True
                    pending_feed_error = str(exc)[:700]
                    feed_title, items = "", []
                    print(
                        "⚠️ Social-Promo: Quelle %s wird gespeichert; Feed momentan nicht erreichbar. "
                        "Nächster automatischer Versuch in 5 Min. (%s)" % (inferred_name, pending_feed_error[:300])
                    )
                else:
                    raise
        except (httpx.HTTPError, ET.ParseError) as exc:
            raise ValueError(_describe_fetch_error(exc, platform)[:850]) from exc
        source_key = str(inferred_name or "").strip().lstrip("@").lower()
        duplicate = next(
            (
                s for s in self.sources.values()
                if s.get("feed_url") == feed_url
                or (str(s.get("platform", "")) == platform and source_key
                    and str(s.get("source_key") or s.get("name", "")).strip().lstrip("@").lower() == source_key)
            ),
            None,
        )
        if duplicate:
            raise ValueError("Diese Quelle ist bereits eingetragen (ID: %s)." % duplicate["id"])
        label = platform_meta(platform)[0]
        now = datetime.now(timezone.utc)
        source = {
            "id": uuid.uuid4().hex[:8].upper(),
            "platform": platform,
            "platform_label": label,
            "name": _clean_text((name or inferred_name or feed_title or label).strip(), 100),
            "custom_name": bool(name and name.strip()),
            "feed_title": _clean_text(feed_title, 100),
            "feed_url": feed_url,
            "creator_id": str(creator_id) if creator_id and str(creator_id).isdigit() else "",
            "added_by": str(added_by),
            "added_at": now.isoformat(timespec="seconds"),
            "enabled": True,
            "initialized": not pending_feed,
            "feed_status": "pending" if pending_feed else "active",
            "source_key": source_key,
            "feed_failures": 1 if pending_feed else 0,
            "retry_after": (now + timedelta(seconds=POLL_FAILURE_INITIAL_BACKOFF_SECONDS)).isoformat(timespec="seconds") if pending_feed else "",
            "last_error": pending_feed_error,
            "last_error_logged_at": now.isoformat(timespec="seconds") if pending_feed else "",
            "seen_ids": [item["id"] for item in items][:MAX_SEEN_IDS],
            "last_item": items[0]["id"] if items else "",
            "last_checked": now.isoformat(timespec="seconds"),
        }
        self.sources[source["id"]] = source
        try:
            self._save_sources()
        except OSError as exc:
            self.sources.pop(source["id"], None)
            raise ValueError("Die Quelle wurde geprüft, konnte aber nicht gespeichert werden. Prüfe Schreibrechte im Hosting.") from exc
        return source, pending_feed

    def set_enabled(self, source_id: str, enabled: bool) -> dict:
        source = self.sources.get(str(source_id).upper())
        if not source:
            raise ValueError("Quelle nicht gefunden.")
        source["enabled"] = bool(enabled)
        self._save_sources()
        return source

    def update_source(self, source_id: str, name: str | None = None, creator_id: str | None = None) -> dict:
        source = self.sources.get(str(source_id).upper())
        if not source:
            raise ValueError("Quelle nicht gefunden.")
        if name is not None and name.strip():
            source["name"] = _clean_text(name.strip(), 100)
            source["custom_name"] = True
        if creator_id is not None:
            source["creator_id"] = str(creator_id) if str(creator_id).isdigit() else ""
        self._save_sources()
        return source

    def remove_source(self, source_id: str) -> dict:
        key = str(source_id).upper()
        if key not in self.sources:
            raise ValueError("Quelle nicht gefunden.")
        removed = self.sources.pop(key)
        try:
            self._save_sources()
        except OSError:
            self.sources[key] = removed
            raise ValueError("Die Quelle konnte nicht entfernt werden. Prüfe die Schreibrechte.")
        return removed

    async def _poll_one(self, source_id: str, source: dict) -> None:
        feed_url = str(source.get("feed_url", ""))
        checked_at = datetime.now(timezone.utc)
        try:
            feed_url, feed_title, items = await self._fetch_feed(
                feed_url, str(source.get("platform", "rss"))
            )
        except asyncio.CancelledError:
            raise
        except (ValueError, httpx.HTTPError, ET.ParseError) as exc:
            # Back off on unhealthy third-party feeds. This avoids hammering public
            # mirrors and keeps the hosting console readable while still retrying.
            failure_count = int(source.get("feed_failures", 0) or 0) + 1
            exponent = min(failure_count - 1, 8)
            retry_seconds = min(
                POLL_FAILURE_MAX_BACKOFF_SECONDS,
                max(
                    POLL_FAILURE_INITIAL_BACKOFF_SECONDS * (2 ** exponent),
                    _retry_after_seconds(exc),
                ),
            )
            error_text = str(exc)[:700]
            previous_log_at = _parse_entry_date(str(source.get("last_error_logged_at", "")))
            should_log = (
                not source.get("last_error_logged_at")
                or checked_at.timestamp() - previous_log_at >= POLL_FAILURE_LOG_INTERVAL_SECONDS
            )
            source["feed_failures"] = failure_count
            source["feed_status"] = "pending" if not source.get("initialized") else "degraded"
            source["last_error"] = error_text
            source["last_checked"] = checked_at.isoformat(timespec="seconds")
            source["retry_after"] = (
                checked_at + timedelta(seconds=retry_seconds)
            ).isoformat(timespec="seconds")
            if should_log:
                print(
                    "⚠️ Social-Promo: Quelle %s nicht abrufbar; nächster Versuch in %d Min. (%s: %s)"
                    % (
                        source.get("name", source_id),
                        max(1, retry_seconds // 60),
                        type(exc).__name__,
                        error_text[:300],
                    )
                )
                source["last_error_logged_at"] = checked_at.isoformat(timespec="seconds")
            self._save_sources()
            return

        source["feed_status"] = "active"
        source["feed_failures"] = 0
        source.pop("retry_after", None)
        source.pop("last_error", None)
        source.pop("last_error_logged_at", None)
        if feed_url != source.get("feed_url"):
            # Remember the working mirror so polling doesn't repeat the failed primary host.
            source["feed_url"] = feed_url
            self._save_sources()
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

        now_ts = datetime.now(timezone.utc).timestamp()
        new_items = [
            item for item in items
            if item["id"] not in stored_seen
            and self._is_video_item(source, item)
            # Feeds sometimes resurface old uploads; never announce videos older than 3 days.
            and is_recent(_parse_entry_date(item.get("published", "")), now_ts)
        ]
        if new_items:
            new_items = sorted(new_items, key=lambda item: _parse_entry_date(item.get("published", "")))
            new_items = new_items[-MAX_POSTS_PER_CYCLE:]
            channel = await self._target_channel()
            if channel is None:
                return
            for item in new_items:
                if not await self._announce(channel, source, item, feed_title):
                    return
                source["last_announced_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                source["announced_count"] = int(source.get("announced_count", 0) or 0) + 1
                await asyncio.sleep(0.5)

        merged = current_ids + [x for x in source.get("seen_ids", []) if x not in current_ids]
        source["seen_ids"] = merged[:MAX_SEEN_IDS]
        source["last_item"] = current_ids[0]
        source["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if feed_title and not source.get("custom_name"):
            source["feed_title"] = _clean_text(feed_title, 100)
        self._save_sources()

    async def _respond_added(self, interaction: discord.Interaction, platform: str, quelle: str, name: str | None, creator):
        try:
            source, pending = await self.add_source(platform, quelle, name, str(creator.id) if creator else None, str(interaction.user.id))
        except ValueError as exc:
            await interaction.followup.send("❌ Quelle nicht hinzugefügt: " + str(exc)[:850], ephemeral=True)
            return
        label = source["platform_label"]
        lines = [
            ("✅ **Creator verknüpft – Feed noch nicht erreichbar**" if pending else "✅ **Creator verknüpft**"),
            f"**Plattform:** {label}",
            f"**Name:** {source['name']}",
            f"**ID:** {source['id']}",
        ]
        if creator:
            lines.append(f"**Discord:** {creator.mention}")
        if pending:
            lines.append("\nPulse prüft die Quelle automatisch erneut. Sobald der Feed erreichbar ist, wird sie aktiviert.")
        else:
            lines.append(f"\nNeue Videos werden ab jetzt automatisch in <#{TARGET_CHANNEL_ID}> mit <@&{SOCIAL_PING_ROLE_ID}> angekündigt. Bereits veröffentlichte Videos werden nicht rückwirkend gepostet.")
        await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="social_link", description="Creator per Link verknüpfen – neue Videos werden automatisch angekündigt")
    @app_commands.describe(
        link="Link zum Kanal/Profil, z. B. https://www.youtube.com/@name oder https://www.tiktok.com/@name",
        creator="Optional: Discord-Mitglied, dem der Kanal gehört (wird in der Ankündigung genannt)",
        name="Optionaler Anzeigename",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def social_link(self, interaction: discord.Interaction, link: str, creator: discord.Member | None = None, name: str | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            platform, quelle = detect_platform(link)
        except ValueError as exc:
            await interaction.followup.send("❌ " + str(exc), ephemeral=True)
            return
        await self._respond_added(interaction, platform, quelle, name, creator)

    @app_commands.command(name="social_add", description="Automatische Video-/Social-Ankündigung hinzufügen")
    @app_commands.describe(
        quelle="YouTube-Kanal/Handle, Social-Profilname oder öffentliche RSS-/Atom-Feed-URL",
        name="Optionaler Anzeigename für die Discord-Werbung",
        creator="Optional: Discord-Mitglied, dem der Kanal gehört",
    )
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
        creator: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._respond_added(interaction, plattform.value, quelle, name, creator)

    @app_commands.command(name="social_list", description="Alle überwachten Social-Videoquellen anzeigen")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def social_list(self, interaction: discord.Interaction):
        if not self.sources:
            await interaction.response.send_message("Es sind noch keine Social-Quellen eingetragen. Nutze /social_add.", ephemeral=True)
            return
        lines = []
        for source in list(self.sources.values())[:40]:
            if not source.get("enabled", True):
                status = "⏸️ pausiert"
            elif source.get("feed_status") == "pending" or not source.get("initialized"):
                status = "🟡 wartet auf Feed"
            elif source.get("feed_status") == "degraded":
                status = "🟠 Feed gestört"
            else:
                status = "🟢 aktiv"
            lines.append(
                source.get("id", "?") + " · " + status + " · **"
                + _clean_text(source.get("name", "Unbenannt"), 100) + "** ("
                + str(source.get("platform_label", source.get("platform", "Social"))) + ")"
            )
        embed = discord.Embed(title="📡 Social-Promo · Quellen", description="\n".join(lines), color=_pulse_color("info"))
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
            # Show exactly how a real announcement looks, clearly marked as a test.
            sample_source = {"platform": "youtube", "name": "Beispiel-Creator", "source_key": "youtube"}
            sample_item = {
                "title": "🧪 Testankündigung – so sehen neue Videos aus",
                "url": "https://www.youtube.com/",
                "description": "Dies ist eine Testnachricht von Pulse. Echte Ankündigungen enthalten Titel, Vorschaubild und Link des neuen Videos.",
                "published": datetime.now(timezone.utc).isoformat(),
            }
            content, embed, view = self.build_announcement(sample_source, sample_item)
            await channel.send(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(id=SOCIAL_PING_ROLE_ID)],
                    users=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
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
