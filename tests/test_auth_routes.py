import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from starlette.requests import Request

import auth_routes


def _make_request(scheme="http", headers=None):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or [])]
    scope = {
        "type": "http",
        "scheme": scheme,
        "headers": raw_headers,
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "server": ("testserver", 80),
    }
    return Request(scope)


class RequestIsHttpsTests(unittest.TestCase):
    """_request_is_https() drives the Secure cookie flag - see
    docs/AXIM_REMOTE_ACCESS.md and the Client/Server Step 6 plan."""

    def test_https_scheme_is_secure(self):
        self.assertTrue(auth_routes._request_is_https(_make_request(scheme="https")))

    def test_plain_http_is_not_secure(self):
        self.assertFalse(auth_routes._request_is_https(_make_request(scheme="http")))

    def test_x_forwarded_proto_https_is_secure(self):
        req = _make_request(scheme="http", headers=[("X-Forwarded-Proto", "https")])
        self.assertTrue(auth_routes._request_is_https(req))

    def test_x_forwarded_proto_http_is_not_secure(self):
        req = _make_request(scheme="http", headers=[("X-Forwarded-Proto", "http")])
        self.assertFalse(auth_routes._request_is_https(req))

    def test_x_forwarded_proto_takes_first_value_in_comma_list(self):
        req = _make_request(scheme="http", headers=[("X-Forwarded-Proto", "https, http")])
        self.assertTrue(auth_routes._request_is_https(req))

    def test_x_forwarded_proto_absent_defaults_to_not_secure(self):
        req = _make_request(scheme="http", headers=[("X-Some-Other-Header", "https")])
        self.assertFalse(auth_routes._request_is_https(req))


if __name__ == "__main__":
    unittest.main()
