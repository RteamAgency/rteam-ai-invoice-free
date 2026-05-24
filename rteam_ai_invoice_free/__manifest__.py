# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
{
    "name": "Auto Extract any Bill PDF/JPEG",
    "version": "18.0.1.2.2",
    "summary": "Extract vendor bill data from PDF/JPG via AI and pre-fill a draft vendor bill.",
    "author": "Rteam",
    "website": "https://rteam.agency",
    "support": "alex@rteam.top",
    "license": "LGPL-3",
    "category": "Accounting/Accounting",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/account_move_views.xml",
        "views/wizard_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
