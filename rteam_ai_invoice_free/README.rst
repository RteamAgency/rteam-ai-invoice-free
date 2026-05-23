Rteam AI Bill Free
==================

AI-powered vendor bill extraction from PDF and JPG files, directly inside
Odoo 17.

Upload a scanned or digital PDF/JPG from any draft vendor bill. The Rteam AI
gateway extracts vendor details, dates, currency, and line items. A confidence
indicator (High / Medium / Low) is shown for each extracted field. Review the
preview and click **Confirm** to pre-fill the draft bill.

Free tier: **15 extractions per company per month**. Upgrade to Rteam AI
Bill Pro for unlimited use.

Features
--------

- **Extract from PDF/JPG** button on draft vendor bills (``in_invoice`` /
  ``in_refund``).
- **Extract Bill from PDF/JPG** entry under *Accounting > Vendors* creates a
  new draft vendor bill in one step (file in, draft bill out).
- AI returns vendor name, VAT, invoice number, invoice date, due date,
  currency, and line items (description, quantity, unit price, tax rate).
- Smart vendor matching: searches ``res.partner`` by VAT first, then by name.
- Smart tax matching: finds purchase tax by percentage rate.
- Per-field confidence indicator derived from 0-100 AI scores.
- Monthly quota (15/month) tracked per company; clear upgrade CTA at limit.
- Gateway URL configurable via **Settings > Accounting > Rteam AI Bill**.

Configuration
-------------

1. Install the module (depends on ``account`` only).
2. Go to **Accounting > Configuration > Settings** to verify the gateway URL
   and check this month's quota.
3. Open any draft vendor bill and click **Extract from PDF/JPG**, or use
   **Accounting > Vendors > Extract Bill from PDF/JPG** to create a new draft
   from a file.

Technical
---------

- Technical name: ``rteam_ai_invoice_free``
- Target Odoo version: 17.0
- License: LGPL-3
- Gateway call uses Python stdlib ``urllib.request`` (no extra dependencies).
- Single mock boundary: ``InvoiceExtractWizard._call_ai_gateway(file_bytes, filename)``.

Support
-------

Email: alex@rteam.top
Website: https://rteam.agency
