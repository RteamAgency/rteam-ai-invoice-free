# Part of Rteam AI Invoice Free. See LICENSE file for full copyright and licensing details.
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.ai_gateway import rteam_ai_extract

_CONFIDENCE_HIGH = 80
_CONFIDENCE_MEDIUM = 50


def _score_to_level(score):
    """Convert 0-100 confidence score to high/medium/low label."""
    if score is None:
        return "low"
    if score >= _CONFIDENCE_HIGH:
        return "high"
    if score >= _CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


class InvoiceExtractWizardLine(models.TransientModel):
    _name = "invoice.extract.wizard.line"
    _description = "AI Invoice Extraction - Line Item"

    wizard_id = fields.Many2one(
        "invoice.extract.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    description = fields.Char(string="Description")
    quantity = fields.Float(string="Quantity", default=1.0)
    price_unit = fields.Float(string="Unit Price")
    tax_rate = fields.Float(string="Tax Rate (%)")
    tax_id = fields.Many2one(
        "account.tax",
        string="Tax",
    )
    account_id = fields.Many2one(
        "account.account",
        string="Account",
    )
    confidence_raw = fields.Integer(string="Confidence Score", default=0)
    confidence = fields.Selection(
        [("high", "High"), ("medium", "Medium"), ("low", "Low")],
        string="Confidence",
        default="low",
    )


class InvoiceExtractWizard(models.TransientModel):
    _name = "invoice.extract.wizard"
    _description = "AI Invoice Extraction Wizard"

    move_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        required=True,
        ondelete="cascade",
    )
    state = fields.Selection(
        [("upload", "Upload"), ("preview", "Preview")],
        string="State",
        default="upload",
    )

    # File upload
    attachment_data = fields.Binary(string="PDF / JPG File", attachment=False)
    attachment_fname = fields.Char(string="Filename")

    # Extracted header fields
    vendor_name = fields.Char(string="Vendor Name")
    vendor_vat = fields.Char(string="Vendor VAT")
    partner_id = fields.Many2one("res.partner", string="Matched Vendor")
    create_partner = fields.Boolean(
        string="Create New Vendor",
        help="No existing vendor matched; a new partner will be created on Confirm.",
    )
    invoice_number = fields.Char(string="Invoice Number")
    invoice_date = fields.Date(string="Invoice Date")
    due_date = fields.Date(string="Due Date")
    currency_id = fields.Many2one("res.currency", string="Currency")

    # Overall header confidence
    confidence_vendor = fields.Selection(
        [("high", "High"), ("medium", "Medium"), ("low", "Low")],
        string="Vendor Confidence",
        default="low",
    )
    confidence_dates = fields.Selection(
        [("high", "High"), ("medium", "Medium"), ("low", "Low")],
        string="Dates Confidence",
        default="low",
    )
    confidence_total = fields.Selection(
        [("high", "High"), ("medium", "Medium"), ("low", "Low")],
        string="Total Confidence",
        default="low",
    )

    line_ids = fields.One2many(
        "invoice.extract.wizard.line",
        "wizard_id",
        string="Invoice Lines",
    )

    # -------------------------------------------------------------------------
    # Mock boundary: all tests patch this method
    # -------------------------------------------------------------------------
    def _call_ai_gateway(self, file_bytes: bytes, filename: str) -> dict:
        """Single gateway call - patch this in tests to avoid network access."""
        return rteam_ai_extract(self.env, file_bytes, filename)

    # -------------------------------------------------------------------------
    # Matching helpers
    # -------------------------------------------------------------------------
    def _match_partner(self, name, vat):
        """Return (partner, create_flag).

        Search order: VAT exact match -> name ilike match -> (None, True).
        """
        Partner = self.env["res.partner"]
        if vat:
            partner = Partner.search([("vat", "=", vat), ("is_company", "=", True)], limit=1)
            if not partner:
                partner = Partner.search([("vat", "=", vat)], limit=1)
            if partner:
                return partner, False
        if name:
            partner = Partner.search([("name", "ilike", name), ("supplier_rank", ">", 0)], limit=1)
            if not partner:
                partner = Partner.search([("name", "ilike", name)], limit=1)
            if partner:
                return partner, False
        return self.env["res.partner"], True

    def _match_tax(self, rate):
        """Return an account.tax for purchase matching the given percentage rate, or empty."""
        if not rate:
            return self.env["account.tax"]
        return self.env["account.tax"].search(
            [
                ("type_tax_use", "=", "purchase"),
                ("amount", "=", rate),
                ("amount_type", "=", "percent"),
                ("company_id", "=", self.move_id.company_id.id),
            ],
            limit=1,
        )

    def _get_expense_account(self):
        """Return a sensible default expense account for vendor bill lines."""
        company = self.move_id.company_id
        # Try the company's default expense account property
        account = self.env["account.account"].search(
            [
                # account.account is multi-company via company_ids (18+)
                ("company_ids", "in", company.id),
                ("account_type", "=", "expense"),
                # Odoo 19: account.account.deprecated was removed in favour of active
                ("active", "=", True),
            ],
            limit=1,
        )
        return account

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------
    def action_extract(self):
        """Call the AI gateway, populate preview fields, and return to the same wizard."""
        self.ensure_one()
        if not self.attachment_data:
            raise UserError(_("Please upload a PDF or JPG file before extracting."))

        file_bytes = base64.b64decode(self.attachment_data)
        filename = self.attachment_fname or "invoice.pdf"

        quota = self.env["rteam.ai.invoice.quota"]._get_or_create_for_company()
        quota.check_quota()

        result = self._call_ai_gateway(file_bytes, filename)

        quota.increment()

        self._populate_from_result(result)

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _populate_from_result(self, result):
        """Fill wizard fields from the AI extraction result dict."""
        confidence = result.get("confidence", {})
        vendor_info = result.get("vendor", {})
        vendor_name = vendor_info.get("name", "")
        vendor_vat = vendor_info.get("vat", "")

        partner, create_partner = self._match_partner(vendor_name, vendor_vat)

        raw_date = result.get("invoice_date")
        raw_due = result.get("due_date")
        invoice_date = fields.Date.from_string(raw_date) if raw_date else False
        due_date = fields.Date.from_string(raw_due) if raw_due else False

        currency_name = result.get("currency", "")
        currency = False
        if currency_name:
            currency = self.env["res.currency"].search([("name", "=", currency_name)], limit=1)

        self.write(
            {
                "state": "preview",
                "vendor_name": vendor_name,
                "vendor_vat": vendor_vat,
                "partner_id": partner.id if partner else False,
                "create_partner": create_partner,
                "invoice_number": result.get("invoice_number", ""),
                "invoice_date": invoice_date,
                "due_date": due_date,
                "currency_id": currency.id if currency else False,
                "confidence_vendor": _score_to_level(confidence.get("vendor")),
                "confidence_dates": _score_to_level(confidence.get("invoice_date")),
                "confidence_total": _score_to_level(confidence.get("total")),
            }
        )

        default_account = self._get_expense_account()
        line_vals = []
        for line in result.get("lines", []):
            rate = line.get("tax_rate", 0)
            tax = self._match_tax(rate)
            raw_score = line.get("confidence", 0)
            line_vals.append(
                {
                    "wizard_id": self.id,
                    "description": line.get("description", ""),
                    "quantity": line.get("quantity", 1.0),
                    "price_unit": line.get("price_unit", 0.0),
                    "tax_rate": rate,
                    "tax_id": tax.id if tax else False,
                    "account_id": default_account.id if default_account else False,
                    "confidence_raw": raw_score,
                    "confidence": _score_to_level(raw_score),
                }
            )
        self.line_ids.unlink()
        if line_vals:
            self.env["invoice.extract.wizard.line"].create(line_vals)

    def action_confirm(self):
        """Write extracted data onto the vendor bill and close the wizard."""
        self.ensure_one()
        if self.state != "preview":
            raise UserError(_("Please run extraction before confirming."))

        move = self.move_id

        if self.create_partner and self.vendor_name:
            partner = self.env["res.partner"].create(
                {
                    "name": self.vendor_name,
                    "vat": self.vendor_vat or False,
                    "supplier_rank": 1,
                    "is_company": True,
                }
            )
        else:
            partner = self.partner_id

        move_vals = {
            "partner_id": partner.id if partner else move.partner_id.id,
            "invoice_date": self.invoice_date or move.invoice_date,
            "invoice_date_due": self.due_date or move.invoice_date_due,
            "ref": self.invoice_number or move.ref,
        }
        if self.currency_id:
            move_vals["currency_id"] = self.currency_id.id

        move.write(move_vals)

        # Remove existing auto-balance line added by Odoo to avoid duplicates,
        # then add the extracted lines.
        move.invoice_line_ids.filtered(lambda line: line.display_type == "product").unlink()

        line_vals_list = []
        for wline in self.line_ids:
            vals = {
                "move_id": move.id,
                # display_type=product marks this as an invoice line (Odoo 17/18);
                # without it the line is not part of invoice_line_ids.
                "display_type": "product",
                "name": wline.description or "/",
                "quantity": wline.quantity,
                "price_unit": wline.price_unit,
                "account_id": (
                    wline.account_id.id
                    if wline.account_id
                    else self._get_expense_account().id
                ),
            }
            if wline.tax_id:
                vals["tax_ids"] = [(4, wline.tax_id.id)]
            line_vals_list.append(vals)

        if line_vals_list:
            # Add via the invoice_line_ids relation so Odoo sets these up as
            # proper invoice lines (direct account.move.line.create does not).
            move.write({
                "invoice_line_ids": [
                    (0, 0, {k: v for k, v in vals.items() if k != "move_id"})
                    for vals in line_vals_list
                ]
            })

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if "default_move_id" not in res and ctx.get("active_model") == "account.move":
            res["default_move_id"] = ctx.get("active_id")
        return res
