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


def test_renewal_ui_and_card_repurchase_controls():
    dashboard = (ROOT / "vps_one" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    account = (ROOT / "vps_one" / "templates" / "account.html").read_text(encoding="utf-8")
    renewal_css = (ROOT / "vps_one" / "static" / "renewal.css").read_text(encoding="utf-8")
    assert "/instances/{{ instance.id }}/renew" in dashboard
    assert "钱包续期" in dashboard and "HashPay 续期" in dashboard
    assert "/account/orders/{{ order.id }}/auto-renew" in account
    assert "自动续费：" in account
    assert "再次购买此套餐" in account
    assert "auto-renew-toggle" in renewal_css


def test_shared_ui_defines_all_supported_preferences():
    themes = (ROOT / "vps_one" / "static" / "themes.css").read_text(encoding="utf-8")
    runtime = (ROOT / "vps_one" / "static" / "ui.js").read_text(encoding="utf-8")
    for skin in ('data-skin="newskin"', 'data-skin="glass"'):
        assert skin in themes
    for value in ('"dashboard"', '"newskin"', '"glass"', '"zh-CN"', '"en"'):
        assert value in runtime
