# Part of Rteam AI Invoice Free. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rteam_ai_invoice_gateway_url = fields.Char(
        string="AI Invoice Gateway URL",
        config_parameter="rteam_ai_invoice.gateway_url",
    )
    rteam_ai_invoice_gateway_key = fields.Char(
        string="AI Invoice Gateway Key",
        related="company_id.rteam_ai_invoice_gateway_key",
        readonly=False,
    )
    rteam_ai_invoice_quota_used = fields.Integer(
        string="Extractions Used This Month",
        compute="_compute_rteam_ai_invoice_quota",
    )
    rteam_ai_invoice_quota_limit = fields.Integer(
        string="Extractions Limit",
        compute="_compute_rteam_ai_invoice_quota",
    )

    @api.depends("company_id")
    def _compute_rteam_ai_invoice_quota(self):
        Quota = self.env["rteam.ai.invoice.quota"]
        for rec in self:
            quota = Quota._get_or_create_for_company(rec.company_id)
            rec.rteam_ai_invoice_quota_used = quota.extractions_used
            rec.rteam_ai_invoice_quota_limit = quota.extractions_limit
