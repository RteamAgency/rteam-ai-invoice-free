# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
import json
import logging
import mimetypes
import urllib.error
import urllib.request

from odoo.exceptions import UserError
from odoo.tools.translate import _

from .signed_container import looks_like_signed_container, unwrap_signed_document

_logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_URL = "https://rteam.agency"
_GATEWAY_PARAM = "rteam_ai_invoice.gateway_url"
# A large multi-row order form / price list makes the model generate many output
# tokens, which can take well over 30s end to end; a short timeout surfaced as a
# urllib read-timeout (HTTP 500 on the wizard). 120s covers the slow tail.
_TIMEOUT_SECONDS = 120
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CSV_MIME = "text/csv"
_SUPPORTED_MIME = ("application/pdf", "image/jpeg", "image/png", _XLSX_MIME, _CSV_MIME)


def _guess_mime(filename: str, file_bytes: bytes) -> str:
    """Resolve the upload's MIME type for the multipart Content-Type.

    The gateway validates the declared type and rejects application/octet-stream,
    so we must send the real one. Magic bytes are authoritative for the binary
    formats (the widget's filename can be missing or generic). XLSX is a ZIP
    container (PK header) shared with docx/zip, so it is disambiguated by the
    .xlsx extension; CSV has no reliable magic and is keyed off the extension too.
    A Google Sheet is uploaded as its XLSX or CSV export, so it needs no special
    handling here.
    """
    head = file_bytes[:8]
    if head[:5] == b"%PDF-":
        return "application/pdf"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    name = (filename or "").lower()
    if name.endswith(".xlsx") and head[:2] == b"PK":
        return _XLSX_MIME
    if name.endswith(".csv"):
        return _CSV_MIME
    guess = mimetypes.guess_type(name)[0]
    if guess in _SUPPORTED_MIME:
        return guess
    return "application/octet-stream"


def _normalize_upload(filename: str, file_bytes: bytes):
    """Return (file_bytes, filename) with any signature envelope unwrapped.

    Bank documents often arrive as a PKCS#7 / CAdES (КЕП) container that keeps a
    ``.pdf`` name but is not a PDF. We unwrap to the embedded document so both the
    type detection and the gateway see real bytes. A non-container is returned
    unchanged. When we unwrap a PDF we also normalise the name to ``.pdf`` so the
    gateway's extension check matches the content.
    """
    if not looks_like_signed_container(file_bytes):
        return file_bytes, filename
    embedded = unwrap_signed_document(file_bytes)
    if not embedded:
        return file_bytes, filename
    _logger.info(
        "Unwrapped signed container %r: %d bytes -> %d bytes",
        filename, len(file_bytes), len(embedded),
    )
    new_name = filename or "invoice"
    if embedded[:5] == b"%PDF-" and not new_name.lower().endswith(".pdf"):
        new_name = "%s.pdf" % new_name
    return embedded, new_name


def is_supported_upload(filename: str, file_bytes: bytes) -> bool:
    """True when the upload looks like a file the gateway can extract.

    Used by the on-the-fly decoder (native "Upload Bill" flow) to decide whether
    to offer AI extraction for an uploaded attachment. Mirrors the gateway's own
    accepted types, so we never offer the decoder for a file it would reject.
    A signed container is judged by the document it carries, not the envelope.
    """
    file_bytes, filename = _normalize_upload(filename, file_bytes)
    return _guess_mime(filename, file_bytes) in _SUPPORTED_MIME


def rteam_ai_extract(env, file_bytes: bytes, filename: str) -> dict:
    """POST file_bytes to the Rteam AI Invoice gateway and return the extraction dict.

    This is the single mock boundary for all tests. Patch this function to avoid
    network calls:  unittest.mock.patch('rteam_ai_invoice_free.services.ai_gateway.rteam_ai_extract')

    Raises UserError on any network or API error so the caller never sees a bare exception.
    """
    # Unwrap a PKCS#7 / CAdES (КЕП) signature envelope to the document it carries
    # before anything else, so a signed bank receipt is sent as a real PDF rather
    # than as the crypto container the gateway cannot read.
    file_bytes, filename = _normalize_upload(filename, file_bytes)

    config = env["ir.config_parameter"].sudo()
    base_url = (config.get_param(_GATEWAY_PARAM, _DEFAULT_GATEWAY_URL)).rstrip("/")
    url = "%s/api/rteam-ai-invoice/extract" % base_url

    # database.uuid identifies this Odoo DB to the gateway's per-DB monthly quota
    # (anti-abuse backstop). Stable per database, not secret.
    db_uuid = config.get_param("database.uuid", "")
    module = (
        env["ir.module.module"].sudo()
        .search([("name", "=", "rteam_ai_invoice_free")], limit=1)
    )
    module_version = module.installed_version or ""

    boundary = "----RteamBoundary"
    body_parts = []
    body_parts.append(("--%s" % boundary).encode())
    body_parts.append(
        ('Content-Disposition: form-data; name="file"; filename="%s"' % filename).encode()
    )
    body_parts.append(("Content-Type: %s" % _guess_mime(filename, file_bytes)).encode())
    body_parts.append(b"")
    body_parts.append(file_bytes)
    body_parts.append(("--%s--" % boundary).encode())
    body = b"\r\n".join(body_parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
            "X-Rteam-Db": db_uuid,
            "X-Rteam-Module-Version": module_version,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        _logger.warning("AI gateway HTTP %s for %s: %s", exc.code, url, exc.read())
        raise UserError(
            _("AI gateway returned an error (HTTP %s). Please try again later.", exc.code)
        ) from exc
    except urllib.error.URLError as exc:
        _logger.warning("AI gateway unreachable at %s: %s", url, exc.reason)
        raise UserError(
            _(
                "AI gateway is unreachable (%s). "
                "Check the gateway URL in Settings or try again later.",
                exc.reason,
            )
        ) from exc

    try:
        result = json.loads(raw)
    except (ValueError, TypeError) as exc:
        _logger.warning("AI gateway returned non-JSON response from %s", url)
        raise UserError(_("AI gateway returned an unexpected response. Please try again.")) from exc

    if isinstance(result, dict) and result.get("error"):
        raise UserError(_("AI gateway error: %s", result["error"]))

    return result
