"""Mock-based regression tests for verified Roblox identity resolution."""
from __future__ import annotations

import unittest

from pulse_melonly import (
    InvalidRobloxUsername,
    RobloxRateLimited,
    RobloxUnavailable,
    RobloxUserNotFound,
    clear_cache,
    lookup_user_id,
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
        _client, factory = factory_for(GET=FakeResponse({"data": [
            {"id": 321, "name": "ActualName", "displayName": "Pretty Name"},
            {"id": 321, "name": "ActualName", "displayName": "Pretty Name"},
        ]}))
        users = await search_users("Actual", client_factory=factory)
        self.assertEqual(users, [{
            "id": "321", "username": "ActualName", "displayName": "Pretty Name",
        }])

    async def test_cache_does_not_keep_duplicate_aliases_as_different_accounts(self):
        response = FakeResponse({"data": [{
            "id": 888, "name": "PlayerABC", "displayName": "P",
        }]})
        client, factory = factory_for(POST=response)
        first = await resolve_username("playerabc", client_factory=factory)
        second = await resolve_username("@PLAYERABC", client_factory=factory)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
