# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_SAMPLE_PDF = base64.b64encode(b"%PDF-1.4 fake content").decode()

_EXTRACTION_RESULT = {
    "vendor": {"name": "Schneider Electric SA", "vat": "FR40542065479"},
    "invoice_number": "INV-2026-00847",
    "invoice_date": "2026-04-15",
    "due_date": "2026-05-15",
    "currency": "EUR",
    "lines": [
        {
            "description": "Industrial Switch XS748T",
            "quantity": 3.0,
            "price_unit": 1247.50,
            "tax_rate": 20.0,
            "confidence": 91,
        },
        {
            "description": "Power Supply Unit 48V",
            "quantity": 1.0,
            "price_unit": 389.00,
            "tax_rate": 20.0,
            "confidence": 78,
        },
    ],
    "totals": {"subtotal": 4131.50, "tax": 826.30, "total": 4957.80},
    "confidence": {
        "vendor": 88,
        "invoice_date": 95,
        "due_date": 92,
        "total": 85,
    },
}


@tagged("post_install", "-at_install")
class TestInvoiceExtractWizard(TransactionCase):
    def setUp(self):
        super().setUp()
        company = self.env.company
        journal = self.env["account.journal"].search(
            [("type", "=", "purchase"), ("company_id", "=", company.id)],
            limit=1,
        )
        self.move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "journal_id": journal.id,
                "company_id": company.id,
            }
        )
        self.wizard = self.env["invoice.extract.wizard"].create(
            {
                "move_id": self.move.id,
                "attachment_data": _SAMPLE_PDF,
                "attachment_fname": "schneider_inv_2026.pdf",
            }
        )
        # Ensure vendor partner exists for VAT matching
        self.vendor = self.env["res.partner"].create(
            {
                "name": "Schneider Electric SA",
                "vat": "FR40542065479",
                "is_company": True,
                "supplier_rank": 1,
            }
        )
        # Ensure a 20% purchase tax exists
        self.tax_20 = self.env["account.tax"].create(
            {
                "name": "VAT 20%",
                "amount": 20.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": company.id,
            }
        )
        # Cache the registry class for patch.object calls
        self._wizard_cls = type(self.wizard)

    # ------------------------------------------------------------------
    # Successful extraction + confirm
    # ------------------------------------------------------------------

    def test_action_extract_populates_wizard(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()

        self.assertEqual(self.wizard.state, "preview")
        self.assertEqual(self.wizard.invoice_number, "INV-2026-00847")
        self.assertEqual(str(self.wizard.invoice_date), "2026-04-15")
        self.assertEqual(str(self.wizard.due_date), "2026-05-15")
        self.assertEqual(self.wizard.partner_id.id, self.vendor.id)
        self.assertFalse(self.wizard.create_partner)
        self.assertEqual(len(self.wizard.line_ids), 2)

    def test_action_extract_sets_line_amounts(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()

        line1 = self.wizard.line_ids[0]
        self.assertAlmostEqual(line1.quantity, 3.0)
        self.assertAlmostEqual(line1.price_unit, 1247.50)
        self.assertEqual(line1.tax_id.id, self.tax_20.id)

    def test_action_extract_confidence_levels(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()

        # vendor confidence 88 -> high
        self.assertEqual(self.wizard.confidence_vendor, "high")
        # line 0 confidence 91 -> high
        self.assertEqual(self.wizard.line_ids[0].confidence, "high")
        # line 1 confidence 78 -> medium
        self.assertEqual(self.wizard.line_ids[1].confidence, "medium")

    def test_action_confirm_creates_move_lines(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()

        self.wizard.action_confirm()

        move = self.move
        invoice_lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(invoice_lines), 2)
        # The wizard does not force a GL account; Odoo computes the correct
        # default expense account. Regression against the old code that forced
        # the first expense account by code ("Cash Discount Loss").
        for line in invoice_lines:
            self.assertTrue(line.account_id, "Odoo should compute a default account")
            self.assertEqual(line.account_id.account_type, "expense")

    def test_action_confirm_sets_vendor(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()
        self.wizard.action_confirm()
        self.assertEqual(self.move.partner_id.id, self.vendor.id)

    def test_action_confirm_sets_dates_and_ref(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()
        self.wizard.action_confirm()
        self.assertEqual(str(self.move.invoice_date), "2026-04-15")
        self.assertEqual(str(self.move.invoice_date_due), "2026-05-15")
        self.assertEqual(self.move.ref, "INV-2026-00847")

    def test_action_confirm_sets_currency(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()
        self.wizard.action_confirm()
        eur = self.env["res.currency"].search([("name", "=", "EUR")], limit=1)
        if eur:
            self.assertEqual(self.move.currency_id.id, eur.id)

    def test_action_confirm_returns_act_window_to_move(self):
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()
        result = self.wizard.action_confirm()
        self.assertEqual(result["res_model"], "account.move")
        self.assertEqual(result["res_id"], self.move.id)

    # ------------------------------------------------------------------
    # Quota integration
    # ------------------------------------------------------------------

    def test_quota_increments_on_success(self):
        Quota = self.env["rteam.ai.invoice.quota"]
        quota_before = Quota._get_or_create_for_company()
        used_before = quota_before.extractions_used
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            self.wizard.action_extract()
        self.assertEqual(quota_before.extractions_used, used_before + 1)

    def test_gateway_failure_raises_user_error(self):
        def _raise(*args, **kwargs):
            raise UserError("AI gateway is unreachable (Connection refused).")

        with patch.object(self._wizard_cls, "_call_ai_gateway", side_effect=_raise):
            with self.assertRaises(UserError):
                self.wizard.action_extract()

    def test_quota_not_incremented_on_gateway_failure(self):
        Quota = self.env["rteam.ai.invoice.quota"]
        quota = Quota._get_or_create_for_company()
        used_before = quota.extractions_used

        def _raise(*args, **kwargs):
            raise UserError("Network error.")

        with patch.object(self._wizard_cls, "_call_ai_gateway", side_effect=_raise):
            with self.assertRaises(UserError):
                self.wizard.action_extract()

        self.assertEqual(quota.extractions_used, used_before)

    def test_quota_exhausted_blocks_extraction(self):
        Quota = self.env["rteam.ai.invoice.quota"]
        quota = Quota._get_or_create_for_company()
        quota.write({"extractions_used": 5, "extractions_limit": 5})
        with self.assertRaises(UserError):
            self.wizard.action_extract()

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_extract_without_file_raises_user_error(self):
        wizard_no_file = self.env["invoice.extract.wizard"].create(
            {"move_id": self.move.id}
        )
        with self.assertRaises(UserError):
            wizard_no_file.action_extract()

    def test_open_from_move_button_defaults_move_id(self):
        # Reproduces the real UI path: the "Extract from PDF" button opens the
        # wizard with default_move_id + active_model/active_id in context, and
        # the web client creates the record with no explicit move_id. A bad
        # default_get (returning a "default_move_id" key) raised
        # KeyError: 'default_move_id' on Odoo 19. move_id must resolve from
        # context. Covers both the default_ context key and the active_id path.
        wizard = (
            self.env["invoice.extract.wizard"]
            .with_context(
                default_move_id=self.move.id,
                active_model="account.move",
                active_id=self.move.id,
            )
            .create({})
        )
        self.assertEqual(wizard.move_id, self.move)

        # active_id-only fallback (no default_move_id in context)
        wizard2 = (
            self.env["invoice.extract.wizard"]
            .with_context(active_model="account.move", active_id=self.move.id)
            .create({})
        )
        self.assertEqual(wizard2.move_id, self.move)

    def test_confirm_without_extract_raises_user_error(self):
        with self.assertRaises(UserError):
            self.wizard.action_confirm()

    def test_new_partner_created_when_no_match(self):
        result = dict(_EXTRACTION_RESULT)
        result["vendor"] = {"name": "Unknown Vendor ZZZXXX", "vat": ""}
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=result):
            self.wizard.action_extract()

        self.assertTrue(self.wizard.create_partner)
        self.wizard.action_confirm()

        partner = self.env["res.partner"].search(
            [("name", "=", "Unknown Vendor ZZZXXX")], limit=1
        )
        self.assertTrue(partner)
        self.assertEqual(self.move.partner_id.id, partner.id)

    # ------------------------------------------------------------------
    # Menu-launched flow: wizard opens without a move_id and creates the
    # draft in_invoice itself on Confirm.
    # ------------------------------------------------------------------

    def test_menu_launched_wizard_has_no_move_id(self):
        # The "Extract Bill from PDF/JPG" menu action passes no default_move_id
        # and no active_model in context. The wizard must open cleanly with an
        # empty move_id.
        wizard = self.env["invoice.extract.wizard"].create({})
        self.assertFalse(wizard.move_id)
        self.assertEqual(wizard.state, "upload")

    def test_menu_extract_uses_env_company_for_tax_match(self):
        # Without a move_id _match_tax must fall back to self.env.company,
        # not raise AttributeError on move_id.company_id.
        wizard = self.env["invoice.extract.wizard"].create({
            "attachment_data": _SAMPLE_PDF,
            "attachment_fname": "menu_inv.pdf",
        })
        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            wizard.action_extract()
        # 20% purchase tax exists in the user's company, so each line should pick it up.
        self.assertTrue(all(line.tax_id == self.tax_20 for line in wizard.line_ids))

    def test_menu_confirm_creates_new_draft_in_invoice(self):
        # End-to-end: menu flow creates a new draft in_invoice on Confirm and
        # pre-fills it with the extracted data. Mirrors the bill-from-list UX.
        wizard = self.env["invoice.extract.wizard"].create({
            "attachment_data": _SAMPLE_PDF,
            "attachment_fname": "menu_inv.pdf",
        })
        moves_before = set(self.env["account.move"].search([]).ids)

        with patch.object(self._wizard_cls, "_call_ai_gateway", return_value=_EXTRACTION_RESULT):
            wizard.action_extract()
        action = wizard.action_confirm()

        new_moves = self.env["account.move"].search([
            ("id", "not in", list(moves_before)),
            ("move_type", "=", "in_invoice"),
        ])
        self.assertEqual(len(new_moves), 1, "menu Confirm should create exactly one draft in_invoice")
        new_move = new_moves
        self.assertEqual(new_move.state, "draft")
        self.assertEqual(new_move.partner_id, self.vendor)
        self.assertEqual(new_move.ref, "INV-2026-00847")
        self.assertEqual(action.get("res_id"), new_move.id)

        product_lines = new_move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(product_lines), 2)
        for line in product_lines:
            self.assertTrue(line.account_id, "Odoo must compute the default expense account")
            self.assertEqual(line.account_id.account_type, "expense")
