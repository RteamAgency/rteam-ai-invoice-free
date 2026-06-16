# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
from odoo import _, models
from odoo.exceptions import UserError

from ..services.ai_gateway import is_supported_upload, rteam_ai_extract

# Vendor documents only - we never touch customer invoices / receipts.
_VENDOR_TYPES = ("in_invoice", "in_refund")

# Priority of our AI decoder in the document-import contest. It must beat the
# enterprise OCR (account_invoice_extract: priority 10 for pdf, 5 otherwise) so
# the free Claude extraction wins on the native "Upload Bill" flow, yet stay
# below a structured e-invoice (UBL / Factur-X / CII = 20), which is machine
# authoritative and needs no AI. On Community there is no competitor at all.
_RTEAM_DECODER_PRIORITY = 15


def _rteam_ai_decode(record, file_data, new):
    """Decoder callable selected by ``_extend_with_attachments``.

    Invoked as ``decoder(record, file_data, new)``. Returns ``None`` on success
    or a short reason string on failure; a returned reason makes the import mixin
    roll back any partial write, so a gateway error leaves the draft bill with
    just its attachment (the user can still retry via the wizard).
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
    # Bills list) and drag-and-drop route the uploaded file through the document
    # import mixin's _extend_with_attachments. We claim supported uploads for our
    # AI decoder there - rather than via _get_edi_decoder - because the enterprise
    # OCR override sits above us in the MRO and short-circuits _get_edi_decoder for
    # pdf/jpg/png without calling super(), which would hide our decoder. Nothing
    # overrides _extend_with_attachments, so this interception is MRO-proof.
    # -------------------------------------------------------------------------
    def _extend_with_attachments(self, files_data, new=False):
        self._rteam_ai_claim_attachments(files_data)
        return super()._extend_with_attachments(files_data, new=new)

    def _rteam_ai_claim_attachments(self, files_data):
        """Pre-assign our AI decoder to each supported upload on an empty vendor bill.

        Setting ``file_data['decoder_info']`` makes the base mixin skip its own
        ``_get_edi_decoder`` lookup for that file and use ours, at a priority that
        outranks the OCR but not a structured e-invoice. A bill that already
        carries lines (e.g. created from a PO) is left untouched.
        """
        if self.move_type not in _VENDOR_TYPES:
            return
        if self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            return
        for file_data in files_data:
            if "decoder_info" in file_data:
                continue
            raw = file_data.get("raw")
            name = file_data.get("name") or ""
            if raw and is_supported_upload(name, raw):
                file_data["decoder_info"] = {
                    "priority": _RTEAM_DECODER_PRIORITY,
                    "decoder": _rteam_ai_decode,
                }

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
