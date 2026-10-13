"""Who the local server answers: services/request_guard.py.

Run with plain Python, no Blender:

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

import unittest

from _load import load_module

guard = load_module("services/request_guard.py")

HOST = "localhost:8080"
FRAMO = "https://framo.app"


class AllowedOrigins(unittest.TestCase):
    def test_framo_origins(self):
        for origin in ("https://framo.app", "https://www.framo.app",
                       "http://localhost:3000", "http://127.0.0.1:3000",
                       "http://localhost", "http://localhost:65535"):
            with self.subTest(origin):
                self.assertTrue(guard.is_allowed_origin(origin))

    def test_everything_else(self):
        for origin in (None, "", "null", "http://framo.app", "https://framo.app.evil.com",
                       "https://evil-framo.app", "https://framo.app:443", "https://framo.app/",
                       "https://sub.framo.app", "https://localhost:3000", "http://localhost:0",
                       "http://localhost:65536", "http://localhost:3000.evil.com",
                       "http://localhost.evil.com", "http://127.0.0.2:3000", "http://[::1]:3000",
                       "http://localhost:3000\n", "HTTPS://FRAMO.APP", "https://framo-app.vercel.app"):
            with self.subTest(origin):
                self.assertFalse(guard.is_allowed_origin(origin))


class Hosts(unittest.TestCase):
    def test_this_server(self):
        for host in ("localhost:8080", "127.0.0.1:8080", "LOCALHOST:8080"):
            self.assertTrue(guard.is_allowed_host(host))

    def test_rebinding_and_others(self):
        for host in (None, "", "evil.com", "evil.com:8080", "localhost", "localhost:8081",
                     "127.0.0.1", "0.0.0.0:8080", "[::1]:8080", "localhost:8080.evil.com"):
            with self.subTest(host):
                self.assertFalse(guard.is_allowed_host(host))


class Decide(unittest.TestCase):
    def test_bad_host_refuses_every_route_even_ping(self):
        for method, path in (("GET", "/ping"), ("GET", "/latest-model"), ("POST", "/command"),
                             ("OPTIONS", "/command"), ("POST", "/upload-model")):
            with self.subTest(path=path, method=method):
                decision = guard.decide(method, path, FRAMO, "attacker.example:8080")
                self.assertFalse(decision.allowed)
                self.assertIsNone(decision.cors_origin)

    def test_allowed_origin_gets_its_own_origin_echoed(self):
        for path in ("/latest-model", "/latest-model-info", "/user-info", "/bridge-info",
                     "/render/abc", "/render/abc/file"):
            d = guard.decide("GET", path, "http://localhost:3000", HOST)
            self.assertTrue(d.allowed)
            self.assertEqual(d.cors_origin, "http://localhost:3000")
        for path in ("/command", "/connect-user", "/disconnect-user", "/upload-model"):
            d = guard.decide("POST", path, FRAMO, HOST)
            self.assertTrue(d.allowed)
            self.assertEqual(d.cors_origin, FRAMO)

    def test_foreign_origin_refused_everywhere_but_ping(self):
        for method, path in (("GET", "/latest-model"), ("GET", "/latest-model-info"),
                             ("GET", "/user-info"), ("GET", "/bridge-info"),
                             ("POST", "/connect-user"), ("POST", "/command"),
                             ("POST", "/upload-model"), ("GET", "/render/x/file")):
            with self.subTest(path=path):
                self.assertFalse(guard.decide(method, path, "https://evil.example", HOST).allowed)
        ping = guard.decide("GET", "/ping", "https://evil.example", HOST)
        self.assertTrue(ping.allowed)
        self.assertIsNone(ping.cors_origin, "a foreign page must not be able to read the answer")

    def test_missing_origin(self):
        self.assertTrue(guard.decide("GET", "/ping", None, HOST).allowed)
        # The add-on's own export POSTs here from Python, with no Origin.
        self.assertTrue(guard.decide("POST", "/upload-model", None, HOST).allowed)
        for method, path in (("GET", "/latest-model"), ("POST", "/command"),
                             ("POST", "/connect-user"), ("GET", "/user-info"),
                             ("GET", "/upload-model"), ("POST", "/ping")):
            with self.subTest(path=path, method=method):
                self.assertFalse(guard.decide(method, path, None, HOST).allowed)

    def test_query_string_does_not_change_the_route(self):
        self.assertTrue(guard.decide("GET", "/ping?x=1", None, HOST).allowed)
        self.assertFalse(guard.decide("POST", "/upload-model?x=/ping", "https://evil.example", HOST).allowed)
        self.assertFalse(guard.decide("GET", "/latest-model?/ping", None, HOST).allowed)

    def test_preflight(self):
        ok = guard.decide("OPTIONS", "/command", FRAMO, HOST)
        self.assertTrue(ok.allowed)
        self.assertEqual(ok.cors_origin, FRAMO)
        self.assertFalse(guard.decide("OPTIONS", "/command", "https://evil.example", HOST).allowed)
        self.assertFalse(guard.decide("OPTIONS", "/ping", None, HOST).allowed)

    def test_empty_origin_is_not_missing(self):
        self.assertFalse(guard.decide("POST", "/upload-model", "", HOST).allowed)


if __name__ == "__main__":
    unittest.main()
