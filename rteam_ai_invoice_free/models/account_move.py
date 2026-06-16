# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
from odoo import models
from odoo.exceptions import UserError

from ..services.ai_gateway import is_supported_upload, rteam_ai_extract

# Vendor documents only - we never touch customer invoices / receipts.
_VENDOR_TYPES = ("in_invoice", "in_refund")


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
    # Bills list) and drag-and-drop route the uploaded attachment through
    # account.move._get_edi_decoder. On Odoo 17/18 that hook returns a decoder
    # callable (or None) - there is no priority mechanism. We offer our AI decoder
    # for a supported upload on an empty vendor bill, but only when nothing stronger
    # already claimed the file:
    #   - a structured e-invoice (UBL / Factur-X) or the enterprise OCR each return
    #     a *named* decoder ahead of us, which we keep (pragmatic: paid OCR wins);
    #   - the base itself returns a no-op ``lambda`` for plain pdf/binary on Odoo 17
    #     that only attaches the file without extracting - we treat that placeholder
    #     as free to take over.
    # On Community there is no OCR competitor, so our decoder simply runs.
    # -------------------------------------------------------------------------
    def _get_edi_decoder(self, file_data, new=False):
        decoder = super()._get_edi_decoder(file_data, new=new)
        if decoder and getattr(decoder, "__name__", "") != "<lambda>":
            return decoder
        if self._rteam_ai_should_decode(file_data):
            return self._rteam_ai_decode
        return decoder

    def _rteam_ai_should_decode(self, file_data):
        """True when this upload is one we should AI-extract: a supported file on
        an empty vendor bill. Pure decision (no super / MRO), so it is unit-testable
        regardless of whether the enterprise OCR is installed."""
        if self.move_type not in _VENDOR_TYPES:
            return False
        # Do not clobber a bill that already carries lines (e.g. created from a PO).
        if self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            return False
        attachment = file_data.get("attachment")
        if not attachment:
            return False
        raw = attachment.raw or b""
        name = file_data.get("filename") or attachment.name or ""
        return bool(raw) and is_supported_upload(name, raw)

    def _rteam_ai_decode(self, invoice, file_data, new):
        """Decoder callable, Odoo 17/18 contract.

        Called as ``decoder(invoice, file_data, new)``; returns True on success or
        False otherwise so the import flow just attaches the file and moves on (a
        gateway error leaves the draft with its attachment, retriable via wizard).
        """
        return invoice._rteam_ai_apply_attachment(file_data)

    def _rteam_ai_call_gateway(self, file_bytes, filename):
        """Single gateway call - patch this in tests to avoid network access."""
        return rteam_ai_extract(self.env, file_bytes, filename)

    def _rteam_ai_apply_attachment(self, file_data):
        """Extract the uploaded attachment and pre-fill this draft bill in place.

        Returns True on success, or False on a quota / gateway failure (the import
        flow then just keeps the attachment on the draft).
        """
        self.ensure_one()
        attachment = file_data.get("attachment")
        if not attachment:
            return False
        raw = attachment.raw or b""
        filename = file_data.get("filename") or attachment.name or "invoice"

        quota = self.env["rteam.ai.invoice.quota"]._get_or_create_for_company()
        # check_quota and the gateway raise UserError on the expected failures
        # (quota exhausted, gateway down). In the decoder path we swallow that and
        # report failure so the upload is not aborted by a hard error.
        try:
            quota.check_quota()
            result = self._rteam_ai_call_gateway(raw, filename)
        except UserError:
            return False
        quota.increment()

        # Reuse the wizard's tested mapping (partner match, taxes, lines) by
        # driving the same code path the user would through the wizard.
        wizard = self.env["invoice.extract.wizard"].create({"move_id": self.id})
        wizard._populate_from_result(result)
        wizard.action_confirm()
        return True
