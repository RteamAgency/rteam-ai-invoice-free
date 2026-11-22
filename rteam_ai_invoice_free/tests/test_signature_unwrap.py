# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..services.ai_gateway import _normalize_upload, is_supported_upload
from ..services.signed_container import (
    looks_like_signed_container,
    unwrap_signed_document,
)

# OIDs mirrored from signed_container so the synthetic envelope is self-contained.
_OID_SIGNED_DATA = b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02"
_OID_PKCS7_DATA = b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x01"

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _der_len(n):
    """Encode a DER definite length (short or long form)."""
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _make_signed_container(payload=_PDF):
    """Build a minimal CMS-shaped envelope: SEQUENCE { signedData-OID, ...,
    pkcs7-data-OID, [0] { OCTET STRING payload } }. Enough for the unwrapper."""
    octet = b"\x04" + _der_len(len(payload)) + payload
    explicit0 = b"\xA0" + _der_len(len(octet)) + octet
    body = _OID_SIGNED_DATA + _OID_PKCS7_DATA + explicit0
    return b"\x30" + _der_len(len(body)) + body


@tagged("post_install", "-at_install")
class TestSignatureUnwrap(TransactionCase):
    """A КЕП / PKCS#7 signed bank document (a .pdf name wrapping a crypto
    envelope) must be unwrapped to its embedded PDF before going to the gateway."""

    def test_detects_signed_container(self):
        self.assertTrue(looks_like_signed_container(_make_signed_container()))
        self.assertFalse(looks_like_signed_container(_PDF))
        self.assertFalse(looks_like_signed_container(b"PK\x03\x04xlsx"))

    def test_unwraps_embedded_pdf(self):
        container = _make_signed_container()
        self.assertEqual(unwrap_signed_document(container), _PDF)

    def test_plain_pdf_passes_through_untouched(self):
        self.assertIsNone(unwrap_signed_document(_PDF))
        data, name = _normalize_upload("invoice.pdf", _PDF)
        self.assertEqual(data, _PDF)
        self.assertEqual(name, "invoice.pdf")

    def test_normalize_unwraps_and_fixes_extension(self):
        # A signed receipt named like a bank export (no .pdf or a .p7s name).
        container = _make_signed_container()
        data, name = _normalize_upload("Receipt_8K41.p7s", container)
        self.assertEqual(data, _PDF)
        self.assertTrue(name.lower().endswith(".pdf"))

    def test_signed_container_is_supported_upload(self):
        # Native "Upload Bill" must claim a signed file by its embedded document.
        container = _make_signed_container()
        self.assertTrue(is_supported_upload("Receipt.pdf", container))
        self.assertTrue(is_supported_upload("Receipt.p7s", container))

    def test_garbage_container_is_not_unwrapped(self):
        # SignedData OID present but no recognisable embedded document -> leave as-is.
        fake = b"\x30\x82\x00\x10" + _OID_SIGNED_DATA + b"\x00\x01\x02\x03"
        self.assertIsNone(unwrap_signed_document(fake))
