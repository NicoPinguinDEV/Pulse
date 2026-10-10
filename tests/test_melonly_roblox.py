"""Mock-based regression tests for verified Roblox identity resolution."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from pulse_melonly import (
    MelonlyStore,
    InvalidRobloxUsername,
    RobloxRateLimited,
    RobloxUnavailable,
    RobloxUserNotFound,
    clear_cache,
    lookup_user_id,
    normalize_search_query,
    normalize_username,
    resolve_username,
    search_users,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses["POST"]

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses["GET"]


def factory_for(**responses):
    client = FakeClient(responses)
    return client, lambda **_kwargs: client


class RobloxIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_cache()

    def test_username_normalization_allows_one_optional_at(self):
        self.assertEqual(normalize_username("  @Spieler_123  "), "Spieler_123")
        self.assertEqual(normalize_username("Player123"), "Player123")

    def test_invalid_usernames_are_rejected(self):
        for value in ("", "@@Player123", "ab", "has space", "name!", "a" * 21):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRobloxUsername):
                    normalize_username(value)

    def test_search_keywords_may_be_display_names_without_becoming_identities(self):
        self.assertEqual(normalize_search_query("  Pretty   Display Name "), "Pretty Display Name")
        self.assertEqual(normalize_search_query("@ActualName"), "ActualName")
        for value in ("", "x", "a" * 51):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRobloxUsername):
                    normalize_search_query(value)

    async def test_resolves_canonical_username_id_and_display_name(self):
        response = FakeResponse({"data": [{
            "requestedUsername": "spieler123",
            "id": 123456,
            "name": "Spieler123",
            "displayName": "Der Spieler",
        }]})
        client, factory = factory_for(POST=response)
        account = await resolve_username("@spieler123", client_factory=factory)
        self.assertEqual(account, {
            "id": "123456",
            "username": "Spieler123",
            "displayName": "Der Spieler",
        })
        self.assertEqual(client.calls[0][0], "POST")
        self.assertIn("/v1/usernames/users", client.calls[0][1])

    async def test_does_not_accept_unrelated_first_search_result(self):
        client, factory = factory_for(POST=FakeResponse({"data": [{
            "id": 999, "name": "DifferentUser", "displayName": "Different",
        }]}))
        with self.assertRaises(RobloxUserNotFound):
            await resolve_username("WantedUser", client_factory=factory, use_cache=False)

    async def test_empty_results_are_not_found(self):
        _client, factory = factory_for(POST=FakeResponse({"data": []}))
        with self.assertRaises(RobloxUserNotFound):
            await resolve_username("NobodyHere", client_factory=factory)

    async def test_rate_limits_are_reported(self):
        _client, factory = factory_for(POST=FakeResponse({}, status_code=429))
        with self.assertRaises(RobloxRateLimited):
            await resolve_username("Player123", client_factory=factory, use_cache=False)

    async def test_server_errors_are_not_silently_treated_as_no_user(self):
        _client, factory = factory_for(POST=FakeResponse({}, status_code=503))
        with self.assertRaises(RobloxUnavailable):
            await resolve_username("Player123", client_factory=factory, use_cache=False)

    async def test_saved_id_can_refresh_canonical_name_after_rename(self):
        _client, factory = factory_for(GET=FakeResponse({
            "id": 456789, "name": "NewUsername", "displayName": "New Display",
        }))
        account = await lookup_user_id("456789", client_factory=factory)
        self.assertEqual(account["id"], "456789")
        self.assertEqual(account["username"], "NewUsername")
        self.assertEqual(account["displayName"], "New Display")

    async def test_search_suggestions_keep_username_separate_from_display_name(self):
        client, factory = factory_for(GET=FakeResponse({"data": [
            {"id": 321, "name": "ActualName", "displayName": "Pretty Name"},
            {"id": 321, "name": "ActualName", "displayName": "Pretty Name"},
        ]}))
        users = await search_users("Pretty Name", client_factory=factory)
        self.assertEqual(users, [{
            "id": "321", "username": "ActualName", "displayName": "Pretty Name",
        }])
        self.assertEqual(client.calls[0][2]["params"]["keyword"], "Pretty Name")
        self.assertEqual(users[0]["username"], "ActualName")  # display name was not used as identity

    async def test_cache_does_not_keep_duplicate_aliases_as_different_accounts(self):
        response = FakeResponse({"data": [{
            "id": 888, "name": "PlayerABC", "displayName": "P",
        }]})
        client, factory = factory_for(POST=response)
        first = await resolve_username("playerabc", client_factory=factory)
        second = await resolve_username("@PLAYERABC", client_factory=factory)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(client.calls), 1)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def write_json(path, value):
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


class MelonlyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp_dir.name) / "logs.json")
        self.lock = threading.RLock()
        self.store = MelonlyStore(self.path, read_json, write_json, self.lock)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def entry(entry_id="log-1"):
        return {
            "id": entry_id,
            "target_user": "PlayerABC",
            "roblox_username": "PlayerABC",
            "roblox_display_name": "Player",
            "roblox_id": "123456",
            "roblox_verified": True,
            "type": "Warn",
            "reason": "Testgrund",
            "moderator_id": "42",
            "created_at": "10.10.2026 12:00",
        }

    def test_create_persists_and_rejects_identical_duplicate(self):
        status, saved = self.store.create(self.entry())
        self.assertEqual(status, "created")
        self.assertEqual(saved["roblox_id"], "123456")

        # Re-open the store to verify this is durable file persistence.
        reopened = MelonlyStore(self.path, read_json, write_json, threading.RLock())
        self.assertEqual(len(reopened.list()), 1)
        self.assertEqual(reopened.get("log-1")["roblox_username"], "PlayerABC")

        status, saved = reopened.create(self.entry("log-2"))
        self.assertEqual(status, "duplicate")
        self.assertIsNone(saved)
        self.assertEqual(len(reopened.list()), 1)

    def test_edit_persists_and_rejects_stale_snapshot(self):
        self.assertEqual(self.store.create(self.entry())[0], "created")
        snapshot = self.store.get("log-1")
        status, updated = self.store.update(
            "log-1", snapshot, {"reason": "Neuer Grund", "edited_at": "10.10.2026 12:05"}
        )
        self.assertEqual(status, "updated")
        self.assertEqual(updated["reason"], "Neuer Grund")

        reopened = MelonlyStore(self.path, read_json, write_json, threading.RLock())
        self.assertEqual(reopened.get("log-1")["reason"], "Neuer Grund")
        status, current = reopened.update("log-1", snapshot, {"reason": "Stale Überschreibung"})
        self.assertEqual(status, "conflict")
        self.assertEqual(current["reason"], "Neuer Grund")

    def test_delete_is_durable_and_repeat_delete_is_safe(self):
        self.assertEqual(self.store.create(self.entry())[0], "created")
        snapshot = self.store.get("log-1")
        status, removed = self.store.delete("log-1", expected_snapshot=snapshot)
        self.assertEqual(status, "deleted")
        self.assertEqual(removed["id"], "log-1")

        reopened = MelonlyStore(self.path, read_json, write_json, threading.RLock())
        self.assertIsNone(reopened.get("log-1"))
        self.assertEqual(reopened.delete("log-1")[0], "missing")

    def test_delete_refuses_to_remove_an_entry_changed_after_confirmation(self):
        self.assertEqual(self.store.create(self.entry())[0], "created")
        stale_snapshot = self.store.get("log-1")
        current = self.store.get("log-1")
        self.assertEqual(self.store.update("log-1", current, {"reason": "Anderer Grund"})[0], "updated")
        status, _removed = self.store.delete("log-1", expected_snapshot=stale_snapshot)
        self.assertEqual(status, "conflict")
        self.assertEqual(self.store.get("log-1")["reason"], "Anderer Grund")



if __name__ == "__main__":
    unittest.main()
