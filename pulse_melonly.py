"""Verified Roblox username/ID resolution used by Pulse Melonly.

Only official Roblox API responses are treated as authoritative. Cached username
lookups expire quickly, and user IDs are never inferred from display names.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_SEARCH_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")
_CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_CACHE_LOCK = threading.RLock()


class RobloxLookupError(Exception):
    """Base exception with a safe, user-facing status and message."""

    http_status = 502


class InvalidRobloxUsername(RobloxLookupError):
    http_status = 400


class RobloxUserNotFound(RobloxLookupError):
    http_status = 404


class RobloxRateLimited(RobloxLookupError):
    http_status = 429


class RobloxUnavailable(RobloxLookupError):
    http_status = 502


def normalize_username(value: Any) -> str:
    """Strip whitespace and one optional @; validate a real Roblox username shape."""
    name = str(value or "").strip()
    if name.startswith("@"):
        name = name[1:]
    if not _USERNAME_RE.fullmatch(name):
        raise InvalidRobloxUsername(
            "Ungültiger Roblox-Username. Erlaubt sind 3–20 Zeichen: A–Z, 0–9 und _."
        )
    return name


def normalize_search_query(value: Any) -> str:
    """Sanitize a search keyword; it may be a display name, never an identity."""
    query = " ".join(str(value or "").strip().split())
    if query.startswith("@"):
        query = query[1:]
    if len(query) < 2 or len(query) > 50 or any(ord(char) < 32 for char in query):
        raise InvalidRobloxUsername("Bitte gib 2–50 gültige Zeichen für die Roblox-Suche ein.")
    return query


def _user_payload(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RobloxUnavailable("Roblox hat eine unerwartete Antwort zurückgegeben.")
    name = str(value.get("name") or "").strip()
    display_name = str(value.get("displayName") or name).strip()
    raw_id = str(value.get("id") or "").strip()
    if not _USERNAME_RE.fullmatch(name) or not raw_id.isdecimal() or int(raw_id) <= 0:
        raise RobloxUnavailable("Roblox hat keine gültige Username-/ID-Zuordnung zurückgegeben.")
    return {"id": str(int(raw_id)), "username": name, "displayName": display_name or name}


def _response_json(response: Any, *, lookup_by_id: bool = False) -> Any:
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        raise RobloxRateLimited("Roblox begrenzt gerade die Anfragen. Bitte kurz warten.")
    if status == 404 and lookup_by_id:
        raise RobloxUserNotFound("Das Roblox-Konto zur gespeicherten ID wurde nicht gefunden.")
    if status < 200 or status >= 300:
        raise RobloxUnavailable("Roblox ist gerade nicht erreichbar. Bitte später erneut versuchen.")
    try:
        return response.json()
    except Exception as exc:
        raise RobloxUnavailable("Roblox hat eine ungültige Antwort zurückgegeben.") from exc


def clear_cache() -> None:
    """Clear short-lived lookup cache; intentionally public for isolated unit tests."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _client(factory: Callable[..., Any] | None):
    if factory is not None:
        return factory(timeout=5.0)
    try:
        import httpx
    except ImportError as exc:
        raise RobloxUnavailable("Die HTTP-Abhängigkeit für Roblox-Abfragen fehlt.") from exc
    return httpx.AsyncClient(timeout=5.0)


async def resolve_username(
    value: Any,
    *,
    client_factory: Callable[..., Any] | None = None,
    use_cache: bool = True,
) -> dict[str, str]:
    """Resolve a username through Roblox's official username-to-ID endpoint."""
    name = normalize_username(value)
    now = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(name.casefold())
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                return dict(cached[1])

    try:
        async with _client(client_factory) as client:
            response = await client.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [name], "excludeBannedUsers": False},
            )
        payload = _response_json(response)
    except RobloxLookupError:
        raise
    except Exception as exc:
        raise RobloxUnavailable("Roblox ist gerade nicht erreichbar. Bitte später erneut versuchen.") from exc

    rows = (payload.get("data", []) or []) if isinstance(payload, dict) else []
    # The endpoint is expected to resolve exact usernames. Refuse a surprising
    # different account rather than binding the input to the first returned row.
    matched = next(
        (row for row in rows if isinstance(row, dict)
         and str(row.get("name") or "").casefold() == name.casefold()),
        None,
    )
    if matched is None:
        raise RobloxUserNotFound(f"Der Roblox-Username „{name}“ wurde nicht gefunden.")

    account = _user_payload(matched)
    if use_cache:
        now = time.monotonic()
        with _CACHE_LOCK:
            _CACHE[name.casefold()] = (now, dict(account))
            _CACHE[account["username"].casefold()] = (now, dict(account))
    return account


async def lookup_user_id(
    user_id: Any,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, str]:
    """Refresh the canonical username/display name of a previously stored Roblox ID."""
    raw_id = str(user_id or "").strip()
    if not raw_id.isdecimal() or int(raw_id) <= 0:
        raise InvalidRobloxUsername("Die gespeicherte Roblox-ID ist ungültig.")
    canonical_id = str(int(raw_id))
    try:
        async with _client(client_factory) as client:
            response = await client.get(f"https://users.roblox.com/v1/users/{canonical_id}")
        payload = _response_json(response, lookup_by_id=True)
    except RobloxLookupError:
        raise
    except Exception as exc:
        raise RobloxUnavailable("Roblox ist gerade nicht erreichbar. Bitte später erneut versuchen.") from exc
    account = _user_payload(payload)
    if account["id"] != canonical_id:
        raise RobloxUnavailable("Roblox hat eine inkonsistente Kontozuordnung zurückgegeben.")
    return account


async def search_users(
    value: Any,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> list[dict[str, str]]:
    """Return official Roblox username search suggestions; suggestions are not identities."""
    query = normalize_search_query(value)
    try:
        async with _client(client_factory) as client:
            response = await client.get(
                "https://users.roblox.com/v1/users/search",
                params={"keyword": query, "limit": 8},
            )
        payload = _response_json(response)
    except RobloxLookupError:
        raise
    except Exception as exc:
        raise RobloxUnavailable("Roblox-Suche ist gerade nicht erreichbar. Bitte später erneut versuchen.") from exc

    rows = (payload.get("data", []) or []) if isinstance(payload, dict) else []
    users: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for row in rows:
        try:
            account = _user_payload(row)
        except RobloxLookupError:
            continue
        if account["id"] in seen_ids:
            continue
        seen_ids.add(account["id"])
        users.append(account)
        if len(users) >= 8:
            break
    return users


class MelonlyStore:
    """Thread-safe JSON-backed Melonly store using the app's shared atomic I/O hooks.

    The lock must be the same re-entrant lock used by the rest of the application
    for this file, so writers cannot race with dashboard mutations.
    """

    def __init__(
        self,
        path: str,
        loader: Callable[[str, Any], Any],
        saver: Callable[[str, Any], Any],
        lock: Any,
    ):
        self.path = path
        self.loader = loader
        self.saver = saver
        self.lock = lock

    def _read_locked(self) -> list:
        rows = self.loader(self.path, [])
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _same_incident(left: Any, right: Any) -> bool:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        return (
            str(left.get("id") or "") != str(right.get("id") or "")
            and str(left.get("roblox_id") or "") == str(right.get("roblox_id") or "")
            and str(left.get("type") or "") == str(right.get("type") or "")
            and str(left.get("reason") or "").strip().casefold()
            == str(right.get("reason") or "").strip().casefold()
            and str(left.get("created_at") or "") == str(right.get("created_at") or "")
        )

    def list(self) -> list[dict[str, Any]]:
        """Return a snapshot of well-formed entries without exposing mutable stored rows."""
        with self.lock:
            return [dict(row) for row in self._read_locked() if isinstance(row, dict)]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = next(
                (item for item in self._read_locked()
                 if isinstance(item, dict) and str(item.get("id") or "") == str(entry_id)),
                None,
            )
            return dict(row) if row is not None else None

    def create(self, entry: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        """Persist a new entry, rejecting an ID collision or identical incident."""
        if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
            return "invalid", None
        with self.lock:
            rows = self._read_locked()
            if any(
                isinstance(row, dict) and str(row.get("id") or "") == str(entry["id"])
                for row in rows
            ):
                return "duplicate", None
            if any(self._same_incident(row, entry) for row in rows):
                return "duplicate", None
            saved = dict(entry)
            rows.append(saved)
            self.saver(self.path, rows)
            return "created", dict(saved)

    def update(
        self,
        entry_id: str,
        expected_snapshot: dict[str, Any],
        changes: dict[str, Any],
        *,
        reject_duplicate: bool = True,
    ) -> tuple[str, dict[str, Any] | None]:
        """Update one entry only when its current stored snapshot has not changed."""
        with self.lock:
            rows = self._read_locked()
            current = next(
                (row for row in rows if isinstance(row, dict) and str(row.get("id") or "") == str(entry_id)),
                None,
            )
            if current is None:
                return "missing", None
            if current != expected_snapshot:
                return "conflict", dict(current)
            candidate = dict(current)
            candidate.update(changes)
            if reject_duplicate and any(
                self._same_incident(row, candidate)
                for row in rows
                if isinstance(row, dict) and str(row.get("id") or "") != str(entry_id)
            ):
                return "duplicate", None
            current.update(changes)
            self.saver(self.path, rows)
            return "updated", dict(current)

    def delete(
        self,
        entry_id: str,
        expected_snapshot: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Permanently delete an entry; supports optimistic concurrency checks."""
        with self.lock:
            rows = self._read_locked()
            index = next(
                (i for i, row in enumerate(rows)
                 if isinstance(row, dict) and str(row.get("id") or "") == str(entry_id)),
                None,
            )
            if index is None:
                return "missing", None
            current = rows[index]
            if expected_snapshot is not None and current != expected_snapshot:
                return "conflict", dict(current)
            removed = dict(current)
            del rows[index]
            self.saver(self.path, rows)
            return "deleted", removed
