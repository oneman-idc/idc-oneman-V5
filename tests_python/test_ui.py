from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
    "account.html",
    "admin.html",
    "admin_plans.html",
    "admin_products.html",
    "admin_refunds.html",
    "dashboard.html",
    "home.html",
    "install.html",
    "login.html",
    "register.html",
    "settings.html",
)


def test_all_frontend_templates_include_shared_ui():
    for name in TEMPLATES:
        source = (ROOT / "vps_one" / "templates" / name).read_text(encoding="utf-8")
        assert '{% include "_ui_head.html" %}' in source, name


def test_shared_ui_defines_all_supported_preferences():
    themes = (ROOT / "vps_one" / "static" / "themes.css").read_text(encoding="utf-8")
    runtime = (ROOT / "vps_one" / "static" / "ui.js").read_text(encoding="utf-8")
    for skin in ('data-skin="newskin"', 'data-skin="glass"'):
        assert skin in themes
    for value in ('"dashboard"', '"newskin"', '"glass"', '"zh-CN"', '"en"'):
        assert value in runtime
