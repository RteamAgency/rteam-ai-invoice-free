# Part of Rteam AI Invoice Free. See LICENSE file for full copyright and licensing details.
import io
import json
import urllib.error
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.rteam_ai_invoice_free.services import ai_gateway

# Patch ONLY the HTTP transport, so the real rteam_ai_extract body runs.
_PATCH_TARGET = "odoo.addons.rteam_ai_invoice_free.services.ai_gateway.urllib.request.urlopen"

_OK_BODY = {
    "vendor": {"name": "ACME", "vat": "FR40542065479"},
    "invoice_number": "INV-1",
    "lines": [],
    "totals": {"subtotal": 0.0, "tax": 0.0, "total": 0.0},
    "confidence": {},
}


class _FakeResp:
    """Context-manager stand-in for the urlopen() return value."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


@tagged("post_install", "-at_install")
class TestGatewayClient(TransactionCase):
    """#6 real-path coverage: patch ONLY the HTTP transport (urlopen), so the real
    rteam_ai_extract executes - reading ir.config_parameter, looking up database.uuid
    and the module version, building the request, parsing the response and mapping
    errors. A too-broad mock (patching the whole _call_ai_gateway) would hide a
    typo'd model name or config key; this test would not."""

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "rteam_ai_invoice.gateway_url", "https://gw.example.test/")

    def test_happy_path_reads_config_and_parses(self):
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["db_header"] = req.headers.get("X-rteam-db")
            captured["has_version_header"] = "X-rteam-module-version" in req.headers
            captured["body"] = req.data
            return _FakeResp(json.dumps(_OK_BODY).encode())

        with patch(_PATCH_TARGET, side_effect=_fake_urlopen):
            result = ai_gateway.rteam_ai_extract(self.env, b"%PDF-1.4", "x.pdf")

        # the configured gateway URL was actually read from ir.config_parameter
        self.assertEqual(
            captured["url"], "https://gw.example.test/api/rteam-ai-invoice/extract")
        # database.uuid + installed module version lookups ran on the real path
        expected_uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid", "")
        self.assertEqual(captured["db_header"], expected_uuid)
        self.assertTrue(captured["has_version_header"])
        # the multipart part must declare the real MIME, not application/octet-stream,
        # or the gateway rejects the upload ("Unsupported file type"). The gateway
        # validates the declared Content-Type, so assert the exact wire format here.
        self.assertIn(b"Content-Type: application/pdf", captured["body"])
        self.assertNotIn(b"application/octet-stream", captured["body"])
        self.assertEqual(result["vendor"]["name"], "ACME")

    def test_mime_detected_from_magic_bytes_and_extension(self):
        cases = [
            (b"%PDF-1.7\n...", "scan.pdf", "application/pdf"),
            (b"\xff\xd8\xff\xe0junk", "photo.bin", "image/jpeg"),
            (b"\x89PNG\r\n\x1a\nrest", "x", "image/png"),
        ]
        for data, fname, expected in cases:
            captured = {}

            def _fake(req, timeout=None, _c=captured):
                _c["body"] = req.data
                return _FakeResp(json.dumps(_OK_BODY).encode())

            with patch(_PATCH_TARGET, side_effect=_fake):
                ai_gateway.rteam_ai_extract(self.env, data, fname)
            self.assertIn(("Content-Type: %s" % expected).encode(), captured["body"])

    def test_http_error_maps_to_user_error(self):
        def _raise(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "Server Error", {}, io.BytesIO(b"boom"))

        with patch(_PATCH_TARGET, side_effect=_raise):
            with self.assertRaises(UserError):
                ai_gateway.rteam_ai_extract(self.env, b"x", "x.pdf")

    def test_unreachable_maps_to_user_error(self):
        def _raise(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        with patch(_PATCH_TARGET, side_effect=_raise):
            with self.assertRaises(UserError):
                ai_gateway.rteam_ai_extract(self.env, b"x", "x.pdf")

    def test_non_json_maps_to_user_error(self):
        with patch(_PATCH_TARGET, side_effect=lambda req, timeout=None: _FakeResp(b"<html>nope</html>")):
            with self.assertRaises(UserError):
                ai_gateway.rteam_ai_extract(self.env, b"x", "x.pdf")

    def test_error_body_maps_to_user_error(self):
        body = json.dumps({"error": "quota exceeded"}).encode()
        with patch(_PATCH_TARGET, side_effect=lambda req, timeout=None: _FakeResp(body)):
            with self.assertRaises(UserError):
                ai_gateway.rteam_ai_extract(self.env, b"x", "x.pdf")
