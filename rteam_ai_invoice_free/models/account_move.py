# Part of Rteam AI Bill Free. See LICENSE file for full copyright and licensing details.
from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_rteam_ai_extract(self):
        """Open the AI extraction wizard for this vendor bill."""
        self.ensure_one()
        return {
            "name": "Extract Bill from PDF/JPG",
            "type": "ir.actions.act_window",
            "res_model": "invoice.extract.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }
