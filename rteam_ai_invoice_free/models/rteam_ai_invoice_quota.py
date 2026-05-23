# Part of Rteam AI Bill Free. See LICENSE file for full copyright and licensing details.
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 15


class RteamAiInvoiceQuota(models.Model):
    _name = "rteam.ai.invoice.quota"
    _description = "AI Bill Extraction Quota"
    _rec_name = "month_key"

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        index=True,
        ondelete="cascade",
        default=lambda self: self.env.company,
    )
    month_key = fields.Char(
        string="Month",
        required=True,
        index=True,
        help="Format: YYYY-MM",
    )
    extractions_used = fields.Integer(string="Extractions Used", default=0)
    extractions_limit = fields.Integer(
        string="Extractions Limit",
        default=_DEFAULT_LIMIT,
    )

    # Odoo 19: the _sql_constraints class attribute is no longer honoured; SQL
    # constraints are declared as models.Constraint descriptors.
    _unique_company_month = models.Constraint(
        "UNIQUE(company_id, month_key)",
        "A quota record already exists for this company and month.",
    )

    @api.model
    def _get_or_create_for_company(self, company=None):
        """Return (or create) the quota record for the given company and current month."""
        if company is None:
            company = self.env.company
        month_key = fields.Date.today().strftime("%Y-%m")
        quota = self.search(
            [("company_id", "=", company.id), ("month_key", "=", month_key)],
            limit=1,
        )
        if not quota:
            quota = self.create(
                {
                    "company_id": company.id,
                    "month_key": month_key,
                }
            )
        return quota

    def check_quota(self):
        """Raise UserError if this company has exhausted its monthly free extractions."""
        self.ensure_one()
        if self.extractions_used >= self.extractions_limit:
            raise UserError(
                _(
                    "You have reached the free limit of %s AI extractions this month. "
                    "Upgrade to Rteam AI Bill Pro for unlimited extractions.",
                    self.extractions_limit,
                )
            )

    def increment(self):
        """Increment the used counter. Call only after a successful extraction."""
        self.ensure_one()
        self.extractions_used += 1

    @api.model
    def _cron_purge_old_quota(self):
        """Remove quota records older than the current month to keep the table lean."""
        current_month = fields.Date.today().strftime("%Y-%m")
        old = self.search([("month_key", "<", current_month)])
        if old:
            _logger.info("Purging %s old AI invoice quota records.", len(old))
            old.unlink()
