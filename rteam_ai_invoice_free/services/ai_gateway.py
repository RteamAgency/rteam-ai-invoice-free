# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
import json
import logging
import mimetypes
import urllib.error
import urllib.request

from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_URL = "https://rteam.agency"
_GATEWAY_PARAM = "rteam_ai_invoice.gateway_url"
_TIMEOUT_SECONDS = 30
_SUPPORTED_MIME = ("application/pdf", "image/jpeg", "image/png")


def _guess_mime(filename: str, file_bytes: bytes) -> str:
    """Resolve the upload's MIME type for the multipart Content-Type.

    The gateway validates the declared type and rejects application/octet-stream,
    so we must send the real one. Magic bytes are authoritative (the binary
    widget's filename can be missing or generic); fall back to the extension.
    """
    head = file_bytes[:8]
    if head[:5] == b"%PDF-":
        return "application/pdf"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    guess = mimetypes.guess_type(filename or "")[0]
    if guess in _SUPPORTED_MIME:
        return guess
    return "application/octet-stream"


def rteam_ai_extract(env, file_bytes: bytes, filename: str) -> dict:
    """POST file_bytes to the Rteam AI Invoice gateway and return the extraction dict.

    This is the single mock boundary for all tests. Patch this function to avoid
    network calls:  unittest.mock.patch('rteam_ai_invoice_free.services.ai_gateway.rteam_ai_extract')

    Raises UserError on any network or API error so the caller never sees a bare exception.
    """
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
