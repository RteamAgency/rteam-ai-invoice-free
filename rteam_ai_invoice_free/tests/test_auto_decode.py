# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_PDF_BYTES = b"%PDF-1.4 fake invoice content"

_EXTRACTION_RESULT = {
    "vendor": {"name": "Schneider Electric SA", "vat": "FR40542065479"},
    "invoice_number": "INV-2026-00847",
    "invoice_date": "2026-04-15",
    "due_date": "2026-05-15",
    "currency": "EUR",
    "lines": [
        {"description": "Industrial Switch", "quantity": 3.0, "price_unit": 1247.5, "tax_rate": 20.0, "confidence": 91},
        {"description": "Power Supply Unit", "quantity": 1.0, "price_unit": 389.0, "tax_rate": 20.0, "confidence": 78},
    ],
    "totals": {"subtotal": 4131.5, "tax": 826.3, "total": 4957.8},
    "confidence": {"vendor": 88, "invoice_date": 95, "due_date": 92, "total": 85},
}


@tagged("post_install", "-at_install")
class TestAutoDecode(TransactionCase):
    """The native 'Upload Bill' / drag-drop flow routes the file through
    account.move._extend_with_attachments; we claim supported uploads there so
    our AI decoder pre-fills the bill on the fly, outranking the enterprise OCR."""

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.journal = self.env["account.journal"].search(
            [("type", "=", "purchase"), ("company_id", "=", self.company.id)], limit=1
        )
        self.vendor = self.env["res.partner"].create(
            {"name": "Schneider Electric SA", "vat": "FR40542065479", "is_company": True, "supplier_rank": 1}
        )
        self.env["account.tax"].create(
            {"name": "VAT 20%", "amount": 20.0, "amount_type": "percent", "type_tax_use": "purchase", "company_id": self.company.id}
        )
        self.move_cls = self.env["account.move"].__class__

    def _new_bill(self, move_type="in_invoice"):
        jtype = "sale" if move_type in ("out_invoice", "out_refund") else "purchase"
        journal = self.journal
        if jtype == "sale":
            journal = self.env["account.journal"].search(
                [("type", "=", "sale"), ("company_id", "=", self.company.id)], limit=1
            )
        return self.env["account.move"].create(
            {"move_type": move_type, "journal_id": journal.id, "company_id": self.company.id}
        )

    def _file_data(self, raw=_PDF_BYTES, name="invoice.pdf", mimetype="application/pdf"):
        return {"raw": raw, "name": name, "mimetype": mimetype}

    # ------------------------------------------------------------------
    # Claim logic: which uploads we assign our decoder to
    # ------------------------------------------------------------------
    def test_claims_supported_vendor_bill_at_priority_15(self):
        move = self._new_bill()
        fd = self._file_data()
        move._rteam_ai_claim_attachments([fd])
        self.assertIn("decoder_info", fd, "a plain PDF on an empty bill should be claimed")
        self.assertEqual(fd["decoder_info"]["priority"], 15, "must outrank OCR (10) and lose to EDI (20)")

    def test_claims_xlsx_upload(self):
        move = self._new_bill()
        fd = self._file_data(raw=b"PK\x03\x04xlsxdata", name="order.xlsx", mimetype="application/octet-stream")
        move._rteam_ai_claim_attachments([fd])
        self.assertIn("decoder_info", fd)

    def test_does_not_claim_customer_invoice(self):
        move = self._new_bill(move_type="out_invoice")
        fd = self._file_data()
        move._rteam_ai_claim_attachments([fd])
        self.assertNotIn("decoder_info", fd)

    def test_does_not_claim_when_bill_already_has_lines(self):
        move = self._new_bill()
        move.write({"invoice_line_ids": [(0, 0, {"display_type": "product", "name": "x", "quantity": 1, "price_unit": 5})]})
        fd = self._file_data()
        move._rteam_ai_claim_attachments([fd])
        self.assertNotIn("decoder_info", fd)

    def test_does_not_claim_unsupported_file(self):
        move = self._new_bill()
        fd = self._file_data(raw=b"PK\x03\x04zipdata", name="archive.zip", mimetype="application/zip")
        move._rteam_ai_claim_attachments([fd])
        self.assertNotIn("decoder_info", fd)

    def test_does_not_override_existing_decoder(self):
        move = self._new_bill()
        fd = self._file_data()
        fd["decoder_info"] = {"priority": 20, "decoder": lambda *a: None}  # e.g. a structured-EDI claim
        move._rteam_ai_claim_attachments([fd])
        self.assertEqual(fd["decoder_info"]["priority"], 20, "must not clobber a higher-priority EDI decoder")

    # ------------------------------------------------------------------
    # Execution: the decoder fills the bill / fails cleanly
    # ------------------------------------------------------------------
    def test_decoder_fills_bill_in_place(self):
        move = self._new_bill()
        with patch.object(self.move_cls, "_rteam_ai_call_gateway", return_value=_EXTRACTION_RESULT):
            reason = move._rteam_ai_apply_attachment(self._file_data())
        self.assertIsNone(reason, "successful decode returns None")
        self.assertEqual(move.partner_id, self.vendor)
        self.assertEqual(move.ref, "INV-2026-00847")
        self.assertEqual(str(move.invoice_date), "2026-04-15")
        lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(lines), 2)

    def test_decoder_increments_quota_on_success(self):
        move = self._new_bill()
        quota = self.env["rteam.ai.invoice.quota"]._get_or_create_for_company()
        used_before = quota.extractions_used
        with patch.object(self.move_cls, "_rteam_ai_call_gateway", return_value=_EXTRACTION_RESULT):
            move._rteam_ai_apply_attachment(self._file_data())
        self.assertEqual(quota.extractions_used, used_before + 1)

    def test_gateway_failure_returns_reason_and_leaves_bill_empty(self):
        move = self._new_bill()
        quota = self.env["rteam.ai.invoice.quota"]._get_or_create_for_company()
        used_before = quota.extractions_used

        def _raise(*args, **kwargs):
            raise UserError("AI gateway is unreachable (Connection refused).")

        with patch.object(self.move_cls, "_rteam_ai_call_gateway", side_effect=_raise):
            reason = move._rteam_ai_apply_attachment(self._file_data())

        self.assertTrue(reason, "a failure must return a non-empty reason string")
        self.assertFalse(move.invoice_line_ids.filtered(lambda line: line.display_type == "product"))
        self.assertEqual(quota.extractions_used, used_before, "quota must not increment on failure")

    def test_quota_exhausted_returns_reason(self):
        move = self._new_bill()
        quota = self.env["rteam.ai.invoice.quota"]._get_or_create_for_company()
        quota.write({"extractions_used": 5, "extractions_limit": 5})
        reason = move._rteam_ai_apply_attachment(self._file_data())
        self.assertTrue(reason)
