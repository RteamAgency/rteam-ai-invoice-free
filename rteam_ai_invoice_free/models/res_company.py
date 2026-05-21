# Part of Rteam AI Invoice Free. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    rteam_ai_invoice_gateway_key = fields.Char(
        string="AI Invoice Gateway Key",
        help="Optional per-company API key override for the Rteam AI Invoice gateway.",
    )
