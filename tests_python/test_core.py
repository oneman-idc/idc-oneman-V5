import base64
import json
import time
import pytest
import vps_one.main as main_module
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from vps_one.database import backfill_accounts, migrate_instance_identity, normalize_plan_nat_port_counts
from vps_one.main import app, consume_vnc_session, containers_by_node, create_vnc_session, decode_clicd_ref, encode_clicd_ref, encrypted_access, instance_access, instance_card, instance_mail_text, node_for_instance, node_from_ref, parse_clicd_nodes, parse_plan_image_choice, plan_image_choice, process_refund
from vps_one.models import Base, Instance, Order, Plan, RefundRequest, Setting, User, WalletEntry, WalletTopUp
from vps_one.security import confirmation_code, confirmation_hash, csrf_token, decrypt, encrypt, hash_password, session_token, valid_confirmation, verify_password
from datetime import datetime, timedelta, timezone
from vps_one.services.accounts import ensure_wallet, generate_login_name, parse_money_cents, payment_amount_cents, post_wallet_entry, refund_eligible
from vps_one.services.clicd import CLICD, CLICDError, container_details, container_items, container_status, enabled_image_items, expiration_date, extract_access, normalize_virtualization, plan_payload, reset_password_value
from vps_one.services.hashpay import HashPay


def test_security_roundtrip():
    password_hash = hash_password("StrongPassword123")
    assert verify_password(password_hash, "StrongPassword123")
    assert not verify_password(password_hash, "wrong")
    assert decrypt(encrypt("secret-value")) == "secret-value"


def test_instance_credentials_are_encrypted_at_rest():
    credentials = {"username": "user-test", "password": "initial-secret", "access_code": "code-1"}
    encrypted = encrypted_access(credentials)
    assert "initial-secret" not in encrypted
    instance = Instance(user_id=1, order_id=1, plan_id=1, name="test", access_json=encrypted)
    assert instance_access(instance) == credentials
    instance.access_json = '{"legacy": true}'
    assert instance_access(instance) == {}


def test_clicd_status_and_sub_user_contract():
    response = {"success": True, "data": {"container": {"id": "ct-1", "state": "online"}, "sub_user": {"username": "user-d7db054c", "initial_password": "temporary-secret", "access_code": "a877d569", "login_url": "http://192.0.2.10:8999/login?code=a877d569"}}}
    assert container_status(response["data"]["container"]) == "running"
    assert container_status({"data": {"power_status": "offline"}}) == "stopped"
    assert extract_access(response) == {"username": "user-d7db054c", "password": "temporary-secret", "access_code": "a877d569", "management_url": "http://192.0.2.10:8999/login?code=a877d569"}


def test_enabled_image_response_normalization():
    response = {"success": True, "data": {"items": [{"template_id": "debian-bookworm", "name": "Debian"}, {"id": "wrong-runtime", "type": "kvm"}, {"name": "missing-id"}]}}
    assert enabled_image_items(response, "lxc") == [{"template_id": "debian-bookworm", "name": "Debian", "id": "debian-bookworm", "type": "lxc"}]
    assert normalize_virtualization(" KVM ") == "kvm"
    assert normalize_virtualization("docker") == ""


@pytest.mark.asyncio
async def test_clicd_templates_query_enabled_images_by_runtime(monkeypatch):
    calls = []

    async def request(self, method, path, data=None, params=None):
        calls.append((method, path, params))
        if params["type"] == "lxc":
            return {"success": True, "data": [{"id": "lxc-debian", "name": "Debian LXC", "type": "lxc"}]}
        return {"success": True, "data": {"images": [{"slug": "kvm-debian", "name": "Debian KVM"}]}}

    async def host_info(self):
        raise AssertionError("templates() must not use host-info runtime fields")

    monkeypatch.setattr(CLICD, "request", request)
    monkeypatch.setattr(CLICD, "host_info", host_info)
    client = CLICD("https://panel.example.com", "token")
    result = await client.templates()
    assert result["data"] == [
        {"id": "lxc-debian", "name": "Debian LXC", "type": "lxc"},
        {"slug": "kvm-debian", "name": "Debian KVM", "id": "kvm-debian", "type": "kvm"},
    ]
    assert calls == [("GET", "/images/enabled", {"type": "lxc"}), ("GET", "/images/enabled", {"type": "kvm"})]
    calls.clear()
    assert (await client.templates("kvm"))["data"][0]["id"] == "kvm-debian"
    assert calls == [("GET", "/images/enabled", {"type": "kvm"})]
    with pytest.raises(CLICDError, match="LXC 或 KVM"):
        await client.templates("docker")


@pytest.mark.asyncio
async def test_clicd_templates_keep_partial_runtime_results(monkeypatch):
    async def request(self, method, path, data=None, params=None):
        if params["type"] == "kvm":
            raise CLICDError("KVM endpoint unavailable")
        return {"data": [{"id": "lxc-debian", "name": "Debian"}]}

    monkeypatch.setattr(CLICD, "request", request)
    client = CLICD("https://panel.example.com", "token")
    result = await client.templates()
    assert [image["id"] for image in result["data"]] == ["lxc-debian"]
    assert result["errors"] == ["KVM: KVM endpoint unavailable"]
    with pytest.raises(CLICDError, match="KVM endpoint unavailable"):
        await client.templates("kvm")


def test_multi_clicd_configuration_and_references():
    nodes = parse_clicd_nodes("https://panel-a.example.com/\nhttps://panel-b.example.com", "token-a\ntoken-b")
    assert [(node.index, node.base_url, node.token) for node in nodes] == [
        (0, "https://panel-a.example.com", "token-a"),
        (1, "https://panel-b.example.com", "token-b"),
    ]
    reference = encode_clicd_ref(nodes[1].base_url, "container:27")
    assert decode_clicd_ref(reference) == ("https://panel-b.example.com", "container:27")
    choice = plan_image_choice(nodes[1], "debian-bookworm")
    assert parse_plan_image_choice(choice) == ("https://panel-b.example.com", "debian-bookworm")
    assert parse_plan_image_choice("legacy-image") == ("", "legacy-image")
    with pytest.raises(CLICDError, match="数量必须一致"):
        parse_clicd_nodes("https://panel-a.example.com\nhttps://panel-b.example.com", "token-a")
    with pytest.raises(CLICDError, match="不能重复"):
        parse_clicd_nodes("https://panel-a.example.com\nhttps://panel-a.example.com/", "token-a\ntoken-b")
    with pytest.raises(CLICDError, match="地址无效"):
        parse_clicd_nodes("panel-a.example.com", "token-a")


@pytest.mark.asyncio
async def test_multi_clicd_aggregation_and_instance_routing(monkeypatch):
    nodes = parse_clicd_nodes("https://panel-a.example.com\nhttps://panel-b.example.com", "token-a\ntoken-b")

    async def containers(self):
        suffix = "a" if "panel-a" in self.base else "b"
        return {"data": [{"uuid": f"ct-{suffix}", "name": f"node-{suffix}", "status": "running"}]}

    monkeypatch.setattr(CLICD, "containers", containers)
    items, errors = await containers_by_node(nodes)
    assert not errors
    assert [(item["uuid"], item["_clicd_node_label"]) for item in items] == [("ct-a", "panel-a.example.com"), ("ct-b", "panel-b.example.com")]
    assert [decode_clicd_ref(item["_clicd_ref"]) for item in items] == [("https://panel-a.example.com", "ct-a"), ("https://panel-b.example.com", "ct-b")]

    instance = Instance(user_id=1, order_id=1, plan_id=1, name="test", clicd_id="ct-b", clicd_node="https://panel-b.example.com")
    selected = await node_for_instance(None, instance, nodes)
    assert selected.base_url == "https://panel-b.example.com"

    class SettingsDb:
        async def get(self, model, key):
            values = {
                "clicd_base_url": "https://panel-b.example.com\nhttps://panel-a.example.com",
                "clicd_token": "token-b\ntoken-a",
            }
            return Setting(key=key, value=values[key], encrypted=False)

    reordered, remote_id = await node_from_ref(SettingsDb(), encode_clicd_ref("https://panel-b.example.com", "ct-b"))
    assert reordered.index == 0
    assert reordered.base_url == "https://panel-b.example.com"
    assert remote_id == "ct-b"


def test_clicd_action_routes_do_not_shadow_specialized_routes():
    paths = {route.path for route in app.routes}
    assert "/instances/{instance_id}/actions/{action}" in paths
    assert "/instances/{instance_id}/snapshot" in paths
    assert "/instances/{instance_id}/port" in paths
    assert "/instances/{instance_id}/vnc-session" in paths
    assert "/instances/{instance_id}/vnc" in paths
    assert "/account" in paths
    assert "/account/wallet/topups" in paths
    assert "/account/orders/{order_id}/refunds" in paths
    assert "/admin/refunds" in paths
    assert "/admin/refunds/{refund_id}/approve" in paths
    assert "/admin/products/{container_ref}/actions/{action}" in paths
    assert "/admin/products/{container_ref}/limits" in paths
    assert "/admin/products/{container_ref}/delete" in paths


@pytest.mark.asyncio
async def test_legacy_instance_unique_id_migration():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for statement in [
            "CREATE TABLE users (id INTEGER PRIMARY KEY)",
            "CREATE TABLE plans (id INTEGER PRIMARY KEY)",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY)",
            "CREATE TABLE instances (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id), plan_id INTEGER NOT NULL REFERENCES plans(id), clicd_id VARCHAR(100) UNIQUE, clicd_node VARCHAR(500) NOT NULL DEFAULT '', name VARCHAR(100) NOT NULL, status VARCHAR(30) NOT NULL, ip VARCHAR(100) NOT NULL, ipv6 VARCHAR(100) NOT NULL DEFAULT '', ssh_port INTEGER NOT NULL, management_url TEXT NOT NULL DEFAULT '', ssh_password TEXT NOT NULL DEFAULT '', access_json TEXT NOT NULL DEFAULT '{}', expires_at DATETIME, last_synced_at DATETIME, created_at DATETIME NOT NULL)",
            "INSERT INTO users (id) VALUES (1)",
            "INSERT INTO plans (id) VALUES (1)",
            "INSERT INTO orders (id) VALUES (1)",
            "INSERT INTO orders (id) VALUES (2)",
            "INSERT INTO instances (id,user_id,order_id,plan_id,clicd_id,clicd_node,name,status,ip,ipv6,ssh_port,management_url,ssh_password,access_json,created_at) VALUES (1,1,1,1,'27','https://panel-a.example.com','a','running','','',22,'','','{}','2026-01-01')",
        ]:
            await conn.execute(text(statement))
        await migrate_instance_identity(conn)
        await conn.execute(text("INSERT INTO instances (id,user_id,order_id,plan_id,clicd_id,clicd_node,name,status,ip,ipv6,ssh_port,management_url,ssh_password,access_json,created_at) VALUES (2,1,2,1,'27','https://panel-b.example.com','b','running','','',22,'','','{}','2026-01-01')"))
        rows = (await conn.execute(text("SELECT clicd_node,clicd_id FROM instances ORDER BY id"))).all()
        assert rows == [("https://panel-a.example.com", "27"), ("https://panel-b.example.com", "27")]
    await engine.dispose()


@pytest.mark.asyncio
async def test_plan_nat_port_count_migration():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE plans (id INTEGER PRIMARY KEY, assign_nat BOOLEAN NOT NULL, port_mapping_count INTEGER NOT NULL)"))
        await conn.execute(text("INSERT INTO plans VALUES (1,1,1),(2,0,12),(3,1,80),(4,1,8)"))
        await normalize_plan_nat_port_counts(conn)
        rows = (await conn.execute(text("SELECT assign_nat,port_mapping_count FROM plans ORDER BY id"))).all()
        assert rows == [(1, 2), (0, 0), (1, 64), (1, 8)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_backfill_adds_unique_login_names_and_wallets():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(6))"))
        await conn.execute(text("CREATE TABLE wallets (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE, currency VARCHAR(8) NOT NULL, balance_cents INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        await conn.execute(text("INSERT INTO users (id,username) VALUES (1,NULL),(2,'MEMBER'),(3,'member'),(4,'member')"))
        await backfill_accounts(conn)
        users = (await conn.execute(text("SELECT username FROM users ORDER BY id"))).scalars().all()
        wallets = (await conn.execute(text("SELECT user_id,balance_cents FROM wallets ORDER BY user_id"))).all()
        assert len(set(users)) == 4
        assert all(len(username) == 6 and username.isalpha() and username.islower() for username in users)
        assert wallets == [(1, 0), (2, 0), (3, 0), (4, 0)]
    await engine.dispose()


def test_account_value_objects_and_refund_window():
    username = generate_login_name({"aaaaaa", "bbbbbb"})
    assert len(username) == 6 and username.isalpha() and username.islower()
    assert parse_money_cents("100.05") == 10005
    assert payment_amount_cents("100.05") == 10005
    assert payment_amount_cents(0.1) == 10
    with pytest.raises(ValueError):
        parse_money_cents("1.001")
    with pytest.raises(ValueError):
        payment_amount_cents("1.001")
    now = datetime.utcnow()
    order = Order(order_no="VP-REFUND", user_id=1, plan_id=1, amount_cents=1999, currency="CNY", status="fulfilled", paid_at=now)
    assert refund_eligible(order, now + timedelta(hours=23, minutes=59))
    assert not refund_eligible(order, now + timedelta(hours=24, seconds=1))
    code = confirmation_code()
    digest = confirmation_hash("RF-1", code)
    assert len(code) == 6 and code.isdigit()
    assert valid_confirmation("RF-1", code, digest)
    assert not valid_confirmation("RF-1", "000000" if code != "000000" else "111111", digest)


@pytest.mark.asyncio
async def test_wallet_ledger_is_balanced_and_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        user = User(username="wallet", email="wallet@example.com", password_hash="hash")
        db.add(user)
        await db.flush()
        wallet = await ensure_wallet(db, user.id)
        credit = await post_wallet_entry(db, wallet, 5000, "topup", "topup", 1, "充值")
        duplicate = await post_wallet_entry(db, wallet, 5000, "topup", "topup", 1, "充值")
        await post_wallet_entry(db, wallet, -1900, "purchase", "order", 1, "购买套餐")
        with pytest.raises(ValueError, match="余额不足"):
            await post_wallet_entry(db, wallet, -4000, "purchase", "order", 2, "余额不足")
        await db.commit()
        assert credit.id == duplicate.id
        assert wallet.balance_cents == 3100
        assert await db.scalar(select(func.count(WalletEntry.id))) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_wallet_topup_checkout_failure_is_persisted(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def checkout_failure(*args, **kwargs):
        raise RuntimeError("HashPay unavailable")

    monkeypatch.setattr(main_module, "hashpay_checkout", checkout_failure)
    async with sessions() as db:
        user = User(username="failed", email="failed@example.com", password_hash="hash")
        db.add(user)
        await db.flush()
        await ensure_wallet(db, user.id)
        await db.commit()
        cookie = session_token(user.id, False)
        request = main_module.Request({
            "type": "http",
            "method": "POST",
            "path": "/account/wallet/topups",
            "headers": [(b"cookie", f"vps_session={cookie}".encode())],
            "client": ("127.0.0.1", 12345),
        })
        with pytest.raises(main_module.HTTPException) as error:
            await main_module.create_wallet_topup(request, csrf_token(cookie), "25.00", db)
        assert error.value.status_code == 502
        topup = (await db.execute(select(WalletTopUp))).scalar_one()
        assert topup.status == "payment_error"
    await engine.dispose()


@pytest.mark.asyncio
async def test_refund_job_deletes_instance_and_credits_wallet_once(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    deleted = []

    class Client:
        async def delete(self, instance_id):
            deleted.append(instance_id)

    class Node:
        def client(self):
            return Client()

    async def fake_node_for_instance(db, instance, nodes=None):
        return Node()

    monkeypatch.setattr(main_module, "node_for_instance", fake_node_for_instance)
    async with sessions() as db:
        user = User(username="refund", email="refund@example.com", password_hash="hash")
        admin = User(username="adminx", email="admin@example.com", password_hash="hash", is_admin=True)
        plan = Plan(name="KVM", slug="refund-kvm", price_cents=2999, cpu=1, memory_mb=512, disk_gb=10)
        db.add_all([user, admin, plan])
        await db.flush()
        order = Order(order_no="VP-REFUND-JOB", user_id=user.id, plan_id=plan.id, amount_cents=2999, currency="CNY", status="fulfilled", paid_at=datetime.utcnow(), fulfilled_at=datetime.utcnow())
        db.add(order)
        await db.flush()
        instance = Instance(user_id=user.id, order_id=order.id, plan_id=plan.id, clicd_id="ct-refund", clicd_node="https://panel.example.com", name="VPS-REFUND", status="running")
        refund = RefundRequest(refund_no="RF-REFUND-JOB", order_id=order.id, user_id=user.id, amount_cents=2999, currency="CNY", status="approved", reviewed_by=admin.id)
        db.add_all([instance, refund])
        await ensure_wallet(db, user.id)
        await db.commit()
        await process_refund(db, refund.id)
        await db.refresh(refund)
        await db.refresh(order)
        await db.refresh(instance)
        wallet = await ensure_wallet(db, user.id)
        assert refund.status == "completed"
        assert order.status == "refunded"
        assert instance.status == "deleted"
        assert wallet.balance_cents == 2999
        assert deleted == ["ct-refund"]
        await process_refund(db, refund.id)
        assert await db.scalar(select(func.count(WalletEntry.id))) == 1
        assert deleted == ["ct-refund"]
    await engine.dispose()


def test_real_clicd_container_contract():
    response = {"success": True, "data": [{"id": 27, "uuid": "d25b9ba6", "name": "KVM-S-1", "ip": "192.168.122.85", "public_ipv4s": [{"address": "192.151.158.3"}], "ipv6": "2001:db8::1", "ssh_port": 0, "ssh_password": "secret", "status": "running", "template": "kvm-debian-bookworm"}]}
    item = container_items(response)[0]
    details = container_details(item)
    assert details == {"id": "d25b9ba6", "name": "KVM-S-1", "virtualization": "kvm", "status": "running", "ip": "192.151.158.3", "ipv6": "2001:db8::1", "ssh_port": 22, "ssh_password": "secret", "operating_system": "kvm-debian-bookworm"}


def test_instance_card_marks_only_kvm_for_vnc():
    instance = Instance(user_id=1, order_id=1, plan_id=1, name="VPS-1", status="running", clicd_id="vm-1")
    order = Order(order_no="VP1", user_id=1, plan_id=1, amount_cents=100, currency="CNY", plan_snapshot=json.dumps({"name": "KVM", "virtualization": "kvm"}))
    plan = Plan(id=1, name="KVM", slug="kvm", price_cents=100, cpu=1, memory_mb=512, disk_gb=10, virtualization="kvm")
    card = instance_card(instance, order, plan, {"id": "vm-1", "name": "kvm-one", "status": "running", "virtualization": "kvm", "template": "debian"})
    assert card["virtualization"] == "kvm"
    assert card["is_kvm"] is True
    lxc_card = instance_card(instance, order, plan, {"id": "vm-1", "name": "lxc-one", "status": "running", "virtualization": "lxc", "template": "debian"})
    assert lxc_card["is_kvm"] is False


def test_vnc_session_is_one_time_and_bound_to_instance():
    token = create_vnc_session(7, 9, "https://panel.example.com", "kvm-one", "ticket-one")
    assert consume_vnc_session(token, 7, 8) is None
    token = create_vnc_session(7, 9, "https://panel.example.com", "kvm-one", "ticket-two")
    session = consume_vnc_session(token, 7, 9)
    assert session and session.clicd_ticket == "ticket-two"
    assert consume_vnc_session(token, 7, 9) is None


@pytest.mark.asyncio
async def test_clicd_webvnc_contract(monkeypatch):
    calls = []

    async def request(self, method, path, data=None, params=None):
        calls.append((method, path, data))
        return {"success": True, "data": {"ticket": "ticket-123"}}

    monkeypatch.setattr(CLICD, "request", request)
    client = CLICD("https://panel.example.com/panel/", "token")
    assert await client.vnc_ticket("KVM S/1") == "ticket-123"
    assert calls == [("POST", "/vnc-ticket", {"container_name": "KVM S/1"})]
    assert client.vnc_websocket_url("KVM S/1") == "wss://panel.example.com/panel/api/vnc?container=KVM+S%2F1"
    assert client.headers["User-Agent"] == "VPS-ONE/1.0"


def test_reset_password_contract():
    assert reset_password_value({"success": True, "data": {"password": "NewPass123456"}}) == "NewPass123456"
    with pytest.raises(CLICDError):
        reset_password_value({"success": False, "message": "failed"})
    with pytest.raises(CLICDError):
        reset_password_value({"success": True, "data": {}})


@pytest.mark.asyncio
async def test_sub_user_and_reset_request_contract(monkeypatch):
    calls = []
    async def request(self, method, path, data=None, params=None):
        calls.append((method, path, data))
        if path == "/sub-user/create":
            return {"success": True, "data": {"username": "user-1", "password": "initial", "access_code": "code-1"}}
        return {"success": True, "data": {"password": data["password"]}}
    monkeypatch.setattr(CLICD, "request", request)
    client = CLICD("https://panel.example.com", "token")
    access = await client.create_sub_user("example-vm")
    assert access["management_url"] == "https://panel.example.com/login?code=code-1"
    assert await client.reset_password("ct-1", "NewPass123456") == "NewPass123456"
    assert calls == [("POST", "/sub-user/create", {"container_name": "example-vm"}), ("POST", "/containers/ct-1/reset-password", {"password": "NewPass123456"})]


def test_clicd_payload_contract():
    class Plan:
        virtualization = "lxc"; clicd_image = "debian-bookworm"; cpu = 2; memory_mb = 2048; disk_gb = 40
        assign_nat = True; port_mapping_count = 2; assign_ipv4 = False; ipv4_count = 0
        assign_ipv6 = True; ipv6_count = 1; network_down_mbps = 200; network_up_mbps = 100
        io_read_mbps = 120; io_write_mbps = 80; traffic_gb = 1000
    payload = plan_payload(Plan(), "VP123", "2097-01-01T00:00:00Z")
    assert payload["vcpu"] == 2
    assert payload["template_id"] == "debian-bookworm"
    assert payload["assign_nat"] is True
    assert payload["port_mapping_count"] == 2
    assert payload["network_up_mbps"] == 100
    assert payload["ssh_password"] == ""
    assert payload["ssh_public_key"] == ""
    assert payload["expires_at"] == "2097-01-01"
    assert "monthly_traffic_gb" not in payload
    Plan.port_mapping_count = 12
    assert plan_payload(Plan(), "VP124", "2097-01-01")["port_mapping_count"] == 12
    Plan.assign_nat = False
    assert plan_payload(Plan(), "VP125", "2097-01-01")["port_mapping_count"] == 0


def test_expiration_date_contract():
    assert expiration_date(datetime(2098, 2, 3, 4, 5, tzinfo=timezone.utc)) == "2098-02-03"
    assert expiration_date("2098-02-03T04:05:06+08:00") == "2098-02-03"
    with pytest.raises(CLICDError):
        expiration_date("not-a-date")
    with pytest.raises(CLICDError):
        expiration_date("2020-01-01")


@pytest.mark.asyncio
async def test_hashpay_create_order_contract(monkeypatch):
    calls = []
    async def request(self, method, path, payload=None):
        calls.append((method, path, payload))
        return {"data": {"orderId": "hp-1", "payUrl": "https://pay.example.com/order/hp-1"}}
    monkeypatch.setattr(HashPay, "request", request)
    result = await HashPay("https://hashpay.example.com", "merchant", "private-key").create({"merchantNo": "VP1", "amount": "19.99"})
    assert result["data"]["payUrl"] == "https://pay.example.com/order/hp-1"
    assert calls == [("POST", "/api/merchant/new", {"merchantNo": "VP1", "amount": "19.99"})]


def test_hashpay_encrypted_callback():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    aes_key = AESGCM.generate_key(bit_length=256)
    iv = b"123456789012"
    message = json.dumps({"timestamp": int(time.time()), "payload": {"merchantNo": "VP1", "amount": 10, "status": "paid"}}).encode()
    encrypted = AESGCM(aes_key).encrypt(iv, message, None)
    wrapped = private.public_key().encrypt(aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    envelope = {"alg": "RSA-OAEP-256+A256GCM", "key": base64.b64encode(wrapped).decode(), "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(encrypted).decode()}
    assert HashPay("", "", pem).decrypt_callback(envelope)["merchantNo"] == "VP1"


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
