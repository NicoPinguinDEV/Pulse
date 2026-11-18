"""M-A3-1: social feed fetches must never reach internal addresses.

Before the fix only the start URL and the *final* URL after all redirects were checked: an intermediate redirect
to http://127.0.0.1:25095/… (or a DNS name that resolves to a private address) was already requested. Sources can
be added with `can_manage_announcements`, which is not admin-only. httpx.MockTransport – no network access.
"""
import asyncio
import socket
import unittest
from unittest.mock import patch

import httpx

from cogs import social_promo
from cogs.social_promo import SocialPromo, _PinnedDNSBackend

PUBLIC = {"feeds.example": "93.184.216.34", "cdn.example": "93.184.216.35", "rebind.example": "10.0.0.7"}


def fake_getaddrinfo(host, *args, **kwargs):
    if host not in PUBLIC:
        raise socket.gaierror("unbekannt")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC[host], 443))]


class FeedFetchSsrfTests(unittest.TestCase):
    def fetch(self, url, routes):
        seen = []

        def handler(request: httpx.Request):
            seen.append(str(request.url))
            target = routes.get(str(request.url))
            if target is None:
                return httpx.Response(200, text="<rss></rss>")
            return httpx.Response(302, headers={"location": target})

        real_client = httpx.AsyncClient
        cog = object.__new__(SocialPromo)
        cog._http_client = None

        async def run():
            try:
                return await cog._get_text(url)
            finally:
                if cog._http_client is not None:
                    await cog._http_client.aclose()

        def mocked_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(**kwargs)

        with patch.object(social_promo.httpx, "AsyncClient", mocked_client), \
                patch.object(socket, "getaddrinfo", fake_getaddrinfo):
            try:
                result = asyncio.run(run())
            except ValueError as exc:
                return seen, exc
        return seen, result

    def test_redirect_to_loopback_is_never_requested(self):
        seen, result = self.fetch("https://feeds.example/rss", {"https://feeds.example/rss": "http://127.0.0.1:25095/admin"})
        self.assertIsInstance(result, ValueError)
        self.assertEqual(seen, ["https://feeds.example/rss"])

    def test_redirect_to_a_name_with_a_private_address_is_never_requested(self):
        seen, result = self.fetch("https://feeds.example/rss", {"https://feeds.example/rss": "https://rebind.example/x"})
        self.assertIsInstance(result, ValueError)
        self.assertEqual(seen, ["https://feeds.example/rss"])

    def test_start_url_with_a_private_address_is_refused(self):
        seen, result = self.fetch("https://rebind.example/rss", {})
        self.assertIsInstance(result, ValueError)
        self.assertEqual(seen, [])

    def test_public_redirects_still_work(self):
        seen, result = self.fetch("https://feeds.example/rss", {"https://feeds.example/rss": "https://cdn.example/rss.xml"})
        self.assertEqual(result, "<rss></rss>")
        self.assertEqual(seen, ["https://feeds.example/rss", "https://cdn.example/rss.xml"])


if __name__ == "__main__":
    unittest.main()



class PinnedBackendTests(unittest.TestCase):
    def test_tcp_connect_uses_only_the_exact_validated_ip_set(self):
        import httpcore

        class FakeBackend:
            def __init__(self):
                self.seen = []

            async def connect_tcp(self, **kwargs):
                self.seen.append(kwargs["host"])
                if kwargs["host"] == "93.184.216.34":
                    raise httpcore.ConnectError("first address unavailable")
                return "connected"

            async def connect_unix_socket(self, **kwargs):
                raise AssertionError("Unix socket must never be used")

            async def sleep(self, seconds):
                return None

        backend = _PinnedDNSBackend()
        fake = FakeBackend()
        backend._real = fake
        backend.pin("Feeds.Example.", 443, ("93.184.216.34", "93.184.216.35"))

        async def run():
            return await backend.connect_tcp("feeds.example", 443)

        self.assertEqual(asyncio.run(run()), "connected")
        self.assertEqual(fake.seen, ["93.184.216.34", "93.184.216.35"])

    def test_missing_pin_fails_closed_without_resolving_again(self):
        import httpcore
        backend = _PinnedDNSBackend()

        async def run():
            with self.assertRaises(httpcore.ConnectError):
                await backend.connect_tcp("unvalidated.example", 443)

        asyncio.run(run())
