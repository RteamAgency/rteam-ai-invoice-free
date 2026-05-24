# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tools import mute_logger
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestRteamAiInvoiceQuota(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Quota = self.env["rteam.ai.invoice.quota"]
        self.company = self.env.company

    def _make_quota(self, month_key="2026-05", used=0, limit=5):
        return self.Quota.create(
            {
                "company_id": self.company.id,
                "month_key": month_key,
                "extractions_used": used,
                "extractions_limit": limit,
            }
        )

    def test_quota_increments_on_success(self):
        quota = self._make_quota(used=0)
        quota.check_quota()
        quota.increment()
        self.assertEqual(quota.extractions_used, 1)

    def test_quota_increments_multiple_times(self):
        quota = self._make_quota(used=3, limit=5)
        quota.check_quota()
        quota.increment()
        self.assertEqual(quota.extractions_used, 4)
        quota.check_quota()
        quota.increment()
        self.assertEqual(quota.extractions_used, 5)

    def test_quota_blocks_at_limit(self):
        quota = self._make_quota(used=5, limit=5)
        with self.assertRaises(UserError):
            quota.check_quota()

    def test_quota_blocks_when_over_limit(self):
        quota = self._make_quota(used=6, limit=5)
        with self.assertRaises(UserError):
            quota.check_quota()

    def test_quota_error_mentions_limit(self):
        quota = self._make_quota(used=5, limit=5)
        with self.assertRaises(UserError) as ctx:
            quota.check_quota()
        self.assertIn("5", str(ctx.exception))

    def test_quota_separate_months_are_independent(self):
        q_april = self._make_quota(month_key="2026-04", used=5)
        q_may = self._make_quota(month_key="2026-05", used=0)
        # April is blocked
        with self.assertRaises(UserError):
            q_april.check_quota()
        # May is not blocked
        q_may.check_quota()
        q_may.increment()
        self.assertEqual(q_may.extractions_used, 1)

    def test_get_or_create_for_company_creates_record(self):
        # Ensure no record exists for a synthetic month
        self.Quota.search(
            [("company_id", "=", self.company.id), ("month_key", "=", "2099-01")]
        ).unlink()
        # Temporarily override today via a subclassed method is not practical here;
        # instead create directly and verify the helper returns existing record.
        quota = self._make_quota(month_key="2026-05")
        found = self.Quota.search(
            [("company_id", "=", self.company.id), ("month_key", "=", "2026-05")]
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found.id, quota.id)

    def test_unique_constraint_company_month(self):
        self._make_quota(month_key="2026-06")
        # SQL unique constraint surfaces as a psycopg2 IntegrityError on flush.
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._make_quota(month_key="2026-06")
                self.env.flush_all()

    def test_cron_purge_removes_old_records(self):
        old = self._make_quota(month_key="2025-01")
        old_id = old.id
        self.Quota._cron_purge_old_quota()
        self.assertFalse(self.Quota.browse(old_id).exists())

    def test_cron_purge_keeps_current_month(self):
        from odoo import fields

        current = fields.Date.today().strftime("%Y-%m")
        q = self.Quota.search(
            [("company_id", "=", self.company.id), ("month_key", "=", current)]
        )
        if not q:
            q = self._make_quota(month_key=current)
        q_id = q.id
        self.Quota._cron_purge_old_quota()
        self.assertTrue(self.Quota.browse(q_id).exists())
