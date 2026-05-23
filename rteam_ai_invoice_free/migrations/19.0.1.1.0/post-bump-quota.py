# Part of Rteam AI Invoice Free. See LICENSE file for full copyright and licensing details.
"""Bump existing per-company monthly quota from 5 to 15.

Only rows that still carry the previous default (5) are lifted; admins who
manually raised the limit are left untouched. Runs idempotently on every
upgrade through 19.0.1.1.0.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        UPDATE rteam_ai_invoice_quota
           SET extractions_limit = 15
         WHERE extractions_limit = 5
        """
    )
    if cr.rowcount:
        _logger.info(
            "rteam_ai_invoice_free: bumped %s quota row(s) from 5 to 15 (new free tier).",
            cr.rowcount,
        )
