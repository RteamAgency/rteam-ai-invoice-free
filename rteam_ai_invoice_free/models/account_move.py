# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
from odoo import _, models
from odoo.exceptions import UserError

from ..services.ai_gateway import is_supported_upload, rteam_ai_extract

# Vendor documents only - we never touch customer invoices / receipts.
_VENDOR_TYPES = ("in_invoice", "in_refund")

# Native + structured-EDI decoders (UBL / Factur-X / CII) return priority 20 and
# must keep winning: a real e-invoice is authoritative. Our AI is the fallback for
# plain scans / spreadsheets, so it offers a lower priority. The highest priority
# decoder across all attachments is the one Odoo runs.
_RTEAM_DECODER_PRIORITY = 10


def _rteam_ai_decode(record, file_data, new):
    """Decoder callable registered via ``_get_edi_decoder``.

    Invoked by ``account.document.import.mixin._extend_with_attachments`` as
    ``decoder(record, file_data, new)``. Returns ``None`` on success or a short
    reason string on failure; a returned reason makes the mixin roll back any
    partial write, so a gateway error leaves the draft bill with just its
    attachment (the user can still retry via the wizard).
    """
    return record._rteam_ai_apply_attachment(file_data)


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_rteam_ai_extract(self):
        """Open the AI extraction wizard for this vendor bill."""
        self.ensure_one()
        return {
            "name": "Extract Bill from File",
            "type": "ir.actions.act_window",
            "res_model": "invoice.extract.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }

    # -------------------------------------------------------------------------
    # On-the-fly extraction: the native "Upload Bill" button (on a PO or in the
    # Bills list) and drag-and-drop both route the uploaded file through the
    # document-import mixin. We register an AI decoder there so the bill is
    # pre-filled the moment the file lands, with no second click or re-upload.
    # -------------------------------------------------------------------------
    def _get_edi_decoder(self, file_data, new=False):
        # Defer to any native / structured-EDI decoder first; only step in when
        # nothing else claims the file. Pure fallback, independent of MRO order.
        decoder = super()._get_edi_decoder(file_data, new=new)
        if decoder:
            return decoder
        if self.move_type not in _VENDOR_TYPES:
            return decoder
        # Do not clobber a bill that already carries lines (e.g. created from a PO).
        if self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            return decoder
        raw = file_data.get("raw")
        name = file_data.get("name") or ""
        if not raw or not is_supported_upload(name, raw):
            return decoder
        return {"priority": _RTEAM_DECODER_PRIORITY, "decoder": _rteam_ai_decode}

    def _rteam_ai_call_gateway(self, file_bytes, filename):
        """Single gateway call - patch this in tests to avoid network access."""
        return rteam_ai_extract(self.env, file_bytes, filename)

    def _rteam_ai_apply_attachment(self, file_data):
        """Extract the uploaded file and pre-fill this draft bill in place.

        Returns None on success, or a reason string on a quota / gateway failure
        so the import mixin rolls back cleanly.
        """
        self.ensure_one()
        raw = file_data.get("raw")
        filename = file_data.get("name") or "invoice"

        quota = self.env["rteam.ai.invoice.quota"]._get_or_create_for_company()
        # check_quota and the gateway raise UserError on the expected failures
        # (quota exhausted, gateway down). In the decoder path we must not bubble
        # a hard error to the upload, so we convert it to a reason string; the
        # import mixin then rolls back and leaves the draft with its attachment.
        try:
            quota.check_quota()
            result = self._rteam_ai_call_gateway(raw, filename)
        except UserError as exc:
            return (exc.args and exc.args[0]) or _("Rteam AI extraction failed")
        quota.increment()

        # Reuse the wizard's tested mapping (partner match, taxes, lines) by
        # driving the same code path the user would through the wizard.
        wizard = self.env["invoice.extract.wizard"].create({"move_id": self.id})
        wizard._populate_from_result(result)
        wizard.action_confirm()
        return None
