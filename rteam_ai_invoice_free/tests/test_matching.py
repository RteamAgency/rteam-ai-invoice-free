# Part of Rteam AI Invoice Free. See LICENSE file for full copyright and licensing details.
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestRteamAiInvoiceMatching(TransactionCase):
    """Tests for vendor/tax/account matching logic in InvoiceExtractWizard."""

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
            {"move_id": self.move.id}
        )

    def _make_partner(self, name, vat=False, supplier=True):
        vals = {"name": name, "is_company": True}
        if vat:
            vals["vat"] = vat
        if supplier:
            vals["supplier_rank"] = 1
        return self.env["res.partner"].create(vals)

    def _make_tax(self, rate, company=None):
        if company is None:
            company = self.env.company
        return self.env["account.tax"].create(
            {
                "name": "Test Tax %s%%" % rate,
                "amount": rate,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": company.id,
            }
        )

    # ------------------------------------------------------------------
    # Vendor matching
    # ------------------------------------------------------------------

    def test_match_partner_by_vat_exact(self):
        partner = self._make_partner("Acme GmbH", vat="DE123456789")
        found, create_flag = self.wizard._match_partner("Acme", "DE123456789")
        self.assertEqual(found.id, partner.id)
        self.assertFalse(create_flag)

    def test_match_partner_by_name_ilike(self):
        partner = self._make_partner("Global Supplies Ltd")
        found, create_flag = self.wizard._match_partner("Global Supplies", None)
        self.assertEqual(found.id, partner.id)
        self.assertFalse(create_flag)

    def test_match_partner_vat_takes_priority_over_name(self):
        by_vat = self._make_partner("Euro Corp", vat="FR987654321")
        self._make_partner("Euro Corp Clone")
        found, create_flag = self.wizard._match_partner("Euro Corp Clone", "FR987654321")
        self.assertEqual(found.id, by_vat.id)
        self.assertFalse(create_flag)

    def test_match_partner_no_match_returns_create_flag(self):
        found, create_flag = self.wizard._match_partner(
            "Totally Unknown Vendor XYZ99", "XX000000000"
        )
        self.assertFalse(found)
        self.assertTrue(create_flag)

    def test_match_partner_empty_inputs_returns_create_flag(self):
        found, create_flag = self.wizard._match_partner("", "")
        self.assertTrue(create_flag)

    # ------------------------------------------------------------------
    # Tax matching
    # ------------------------------------------------------------------

    def test_match_tax_by_rate_20(self):
        tax = self._make_tax(20.0)
        found = self.wizard._match_tax(20.0)
        self.assertEqual(found.id, tax.id)

    def test_match_tax_by_rate_7_5(self):
        tax = self._make_tax(7.5)
        found = self.wizard._match_tax(7.5)
        self.assertEqual(found.id, tax.id)

    def test_match_tax_no_match_returns_empty(self):
        found = self.wizard._match_tax(99.99)
        self.assertFalse(found)

    def test_match_tax_zero_rate_returns_empty(self):
        found = self.wizard._match_tax(0)
        self.assertFalse(found)

    def test_match_tax_none_returns_empty(self):
        found = self.wizard._match_tax(None)
        self.assertFalse(found)

    # ------------------------------------------------------------------
    # Default account
    # ------------------------------------------------------------------

