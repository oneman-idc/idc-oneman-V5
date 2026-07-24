import asyncio
import base64
import binascii
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .config import settings
from .database import SessionLocal, init_db, session, write_lock
from .models import Audit, Instance, Job, Order, PaymentEvent, Plan, User
from .security import csrf_token, decrypt, encrypt, hash_password, read_session, session_token, valid_csrf, verify_password
from .services.clicd import CLICD, CLICDError, container_details, container_items, container_status, extract_access, plan_payload, unwrap_data
from .services.hashpay import HashPay
from .services.mailer import send_mail
from .services.settings import get, set_many

cfg = settings()
root = Path(__file__).parent
templates = Jinja2Templates(root / "templates")
rate_buckets: dict[str, list[float]] = {}
logger = logging.getLogger("vps_one.worker")


@dataclass(frozen=True)
class CLICDNode:
    index: int
    base_url: str
    token: str

    @property
    def label(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url

    def client(self) -> CLICD:
        return CLICD(self.base_url, self.token)


def setting_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def parse_clicd_nodes(base_urls: str, tokens: str) -> list[CLICDNode]:
    urls = [value.rstrip("/") for value in setting_lines(base_urls)]
    keys = setting_lines(tokens)
    if not urls and not keys:
        return []
    if len(urls) != len(keys):
        raise CLICDError("CLICD 面板地址与 API Key 数量必须一致，并按行一一对应")
    if len(set(urls)) != len(urls):
        raise CLICDError("CLICD 面板地址不能重复")
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CLICDError(f"CLICD 面板地址无效：{url}")
    return [CLICDNode(index, url, keys[index]) for index, url in enumerate(urls)]


async def clicd_nodes(db) -> list[CLICDNode]:
    nodes = parse_clicd_nodes(await get(db, "clicd_base_url"), await get(db, "clicd_token"))
    if not nodes:
        raise CLICDError("CLICD 尚未配置")
    return nodes


def find_clicd_node(nodes: list[CLICDNode], base_url: str = "") -> CLICDNode:
    normalized = (base_url or "").rstrip("/")
    if normalized:
        node = next((item for item in nodes if item.base_url == normalized), None)
        if not node:
            raise CLICDError(f"CLICD 节点已停用或不存在：{normalized}")
        return node
    return nodes[0]


def encode_clicd_ref(base_url: str, container_id: str) -> str:
    value = f"{base_url.rstrip('/')}\0{container_id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode_clicd_ref(value: str) -> tuple[str, str]:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        base_url, container_id = decoded.split("\0", 1)
        if not base_url or not container_id:
            raise ValueError
        return base_url, container_id
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "CLICD 容器引用无效") from exc


def container_id(item: dict) -> str:
    return str(item.get("uuid") or item.get("id") or item.get("container_id") or "")


def plan_image_choice(node: CLICDNode, image_id: str) -> str:
    return json.dumps([node.base_url, image_id], ensure_ascii=False, separators=(",", ":"))


def parse_plan_image_choice(value: str) -> tuple[str, str]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and len(parsed) == 2 and all(isinstance(item, str) and item for item in parsed):
            return parsed[0].rstrip("/"), parsed[1]
    except (TypeError, ValueError):
        pass
    return "", value


async def node_for_instance(db, instance: Instance, nodes: list[CLICDNode] | None = None) -> CLICDNode:
    nodes = nodes or await clicd_nodes(db)
    if instance.clicd_node:
        return find_clicd_node(nodes, instance.clicd_node)
    plan = await db.get(Plan, instance.plan_id)
    if plan and plan.clicd_node:
        node = find_clicd_node(nodes, plan.clicd_node)
        instance.clicd_node = node.base_url
        return node
    if len(nodes) == 1:
        instance.clicd_node = nodes[0].base_url
        return nodes[0]
    remote_items, errors = await containers_by_node(nodes)
    if errors:
        raise CLICDError("旧实例归属探测未覆盖全部 CLICD 节点，请恢复节点后重试")
    matching_urls = {item["_clicd_node"] for item in remote_items if container_id(item) == str(instance.clicd_id)}
    matches = [node for node in nodes if node.base_url in matching_urls]
    if len(matches) != 1:
        raise CLICDError("旧实例无法唯一匹配 CLICD 节点，请先为其套餐绑定正确节点")
    instance.clicd_node = matches[0].base_url
    return matches[0]


async def containers_by_node(nodes: list[CLICDNode]) -> tuple[list[dict], list[str]]:
    results = await asyncio.gather(*(node.client().containers() for node in nodes), return_exceptions=True)
    containers: list[dict] = []
    errors: list[str] = []
    for node, result in zip(nodes, results):
        if isinstance(result, Exception):
            errors.append(f"{node.label}: {result}")
            continue
        for item in container_items(result):
            remote_id = container_id(item)
            if remote_id:
                containers.append({**item, "_clicd_node": node.base_url, "_clicd_node_label": node.label, "_clicd_ref": encode_clicd_ref(node.base_url, remote_id)})
    return containers, errors


async def node_from_ref(db, value: str) -> tuple[CLICDNode, str]:
    base_url, remote_id = decode_clicd_ref(value)
    nodes = await clicd_nodes(db)
    try:
        return find_clicd_node(nodes, base_url), remote_id
    except CLICDError as exc:
        raise HTTPException(400, "CLICD 节点引用已失效，请刷新页面后重试") from exc


def current(request: Request):
    return read_session(request.cookies.get("vps_session"))


def guard(request: Request, admin: bool = False):
    user = current(request)
    if not user or (admin and not user.get("admin")):
        raise HTTPException(401, "请先登录")
    return user


def check_csrf(request: Request, value: str):
    if not valid_csrf(request.cookies.get("vps_session", ""), value):
        raise HTTPException(419, "CSRF 校验失败")


def limit(request: Request, key: str, maximum: int, window: int = 60):
    now = time.monotonic()
    bucket_key = f"{key}:{request.client.host if request.client else 'unknown'}"
    hits = [stamp for stamp in rate_buckets.get(bucket_key, []) if now - stamp < window]
    if len(hits) >= maximum:
        raise HTTPException(429, "请求过于频繁，请稍后重试")
    hits.append(now)
    rate_buckets[bucket_key] = hits


def ctx(request: Request, **values):
    user = current(request)
    return {
        "request": request,
        "user": user,
        "csrf": csrf_token(request.cookies.get("vps_session", "")) if user else "",
        **values,
    }


async def site_url(db) -> str:
    return (await get(db, "site_url", cfg.base_url)).rstrip("/")


def unwrap(result):
    return result.get("data", result) if isinstance(result, dict) else result


def plan_snapshot(plan: Plan) -> str:
    fields = ["name", "description", "price_cents", "currency", "months", "cpu", "memory_mb", "disk_gb", "traffic_gb", "network_down_mbps", "network_up_mbps", "virtualization", "clicd_node", "clicd_image"]
    return json.dumps({field: getattr(plan, field) for field in fields}, ensure_ascii=False)


def encrypted_access(credentials: dict[str, str]) -> str:
    clean = {key: str(value) for key, value in credentials.items() if value not in {None, ""}}
    return encrypt(json.dumps(clean, ensure_ascii=False)) if clean else ""


def instance_access(instance: Instance) -> dict[str, str]:
    if not instance.access_json:
        return {}
    try:
        value = json.loads(decrypt(instance.access_json))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def safe_status_label(status: str) -> str:
    return {"running": "运行中", "stopped": "已关机", "starting": "启动中", "stopping": "关机中", "restarting": "重启中", "creating": "创建中", "provisioning": "部署中", "pending": "等待中"}.get(status, "状态未知")


def stored_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return decrypt(value)
    except Exception:
        return value


def snapshot_data(order: Order, plan: Plan) -> dict:
    try:
        value = json.loads(order.plan_snapshot or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) and value else {field: getattr(plan, field, "") for field in ("name", "cpu", "memory_mb", "disk_gb", "traffic_gb", "network_down_mbps", "network_up_mbps", "clicd_image")}


def instance_card(instance: Instance, order: Order, plan: Plan, remote: dict | None = None) -> dict:
    details = container_details(remote or {})
    access = instance_access(instance)
    package = snapshot_data(order, plan)
    return {
        "instance": instance,
        "package": package,
        "operating_system": details.get("operating_system") or package.get("clicd_image") or "未返回",
        "ip": details.get("ip") or instance.ip or "未分配",
        "ipv6": details.get("ipv6") or instance.ipv6 or "未分配",
        "ssh_port": details.get("ssh_port") or instance.ssh_port or 22,
        "ssh_password": details.get("ssh_password") or stored_secret(instance.ssh_password) or "未返回",
        "access": access,
    }


def instance_mail_text(card: dict, order: Order) -> str:
    instance, package, access = card["instance"], card["package"], card["access"]
    expiry = instance.expires_at.strftime("%Y-%m-%d") if instance.expires_at else "请登录客户中心查看"
    return f"""您的云主机 SSH 密码已重置。

订单：{order.order_no}
实例：{instance.name}
容器标识：{instance.clicd_id}
套餐：{package.get('name') or '未返回'}
配置：{package.get('cpu') or 0} 核 / {package.get('memory_mb') or 0} MB 内存 / {package.get('disk_gb') or 0} GB 磁盘
流量与带宽：{package.get('traffic_gb') or 0} GB / 下行 {package.get('network_down_mbps') or 0} Mbps / 上行 {package.get('network_up_mbps') or 0} Mbps
操作系统：{card['operating_system']}
公网 IP：{card['ip']}
IPv6：{card['ipv6']}
SSH 端口：{card['ssh_port']}
SSH 密码：{card['ssh_password']}
到期日期：{expiry}

子用户用户名：{access.get('username') or '未返回'}
初始密码：{access.get('password') or '未返回'}
访问码：{access.get('access_code') or '未返回'}
访问链接：{access.get('management_url') or '未返回'}

请妥善保存以上敏感信息。"""


async def process_job(db, job: Job):
    if job.kind == "provision":
        await provision(db, job.ref_id)
    elif job.kind == "mail_instance":
        instance = await db.get(Instance, job.ref_id)
        if not instance:
            raise RuntimeError("邮件任务对应的实例不存在")
        order = await db.get(Order, instance.order_id)
        user = await db.get(User, order.user_id) if order else None
        plan = await db.get(Plan, instance.plan_id)
        if not order or not user or not plan:
            raise RuntimeError("邮件任务缺少订单、用户或套餐数据")
        expiry = instance.expires_at.strftime("%Y-%m-%d") if instance.expires_at else "请登录客户中心查看"
        access = instance_access(instance)
        await send_mail(db, user.email, f"您的 VPS {instance.name} 已交付", f"套餐：{plan.name}\n订单：{order.order_no}\n实例：{instance.name}\n状态：{safe_status_label(instance.status)}\n到期日期：{expiry}\n\nCLICD 管理用户名：{access.get('username') or '未返回'}\n初始密码：{access.get('password') or '未返回'}\n访问码：{access.get('access_code') or '未返回'}\n管理地址：{access.get('management_url') or '请登录客户中心查看'}\n\n以上信息仅发送给订单注册邮箱，请妥善保存并及时修改初始密码。")


async def recover_stale_jobs(db) -> int:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    jobs = (await db.execute(select(Job).where(Job.status == "running", Job.locked_at < cutoff))).scalars().all()
    for job in jobs:
        job.status = "pending"
        job.error = "任务执行进程异常退出，已自动恢复"
        job.run_after = datetime.utcnow()
        job.locked_at = None
    if jobs:
        await db.commit()
    return len(jobs)


async def worker():
    while True:
        job = None
        try:
            async with SessionLocal() as db:
                async with write_lock:
                    await recover_stale_jobs(db)
                    job = (await db.execute(select(Job).where(Job.status == "pending", Job.run_after <= datetime.utcnow()).order_by(Job.id).limit(1))).scalar_one_or_none()
                    if job:
                        job.status = "running"
                        job.locked_at = datetime.utcnow()
                        job.attempts += 1
                        await db.commit()
                if job:
                    try:
                        await process_job(db, job)
                        job.status, job.error, job.locked_at = "done", "", None
                    except Exception as exc:
                        job.status = "pending" if job.attempts < 5 else "failed"
                        job.error = str(exc)[:1000]
                        job.run_after = datetime.utcnow() + timedelta(seconds=min(900, 2 ** job.attempts * 10))
                        job.locked_at = None
                        logger.warning("任务 %s/%s 执行失败：%s", job.kind, job.ref_id, job.error)
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("后台任务循环异常")
        await asyncio.sleep(2)


async def provision(db, order_id: int):
    order = await db.get(Order, order_id)
    if not order or order.status not in {"paid", "provisioning"}:
        return
    plan = await db.get(Plan, order.plan_id)
    existing = (await db.execute(select(Instance).where(Instance.order_id == order.id))).scalar_one_or_none()
    expires = existing.expires_at if existing and existing.expires_at else datetime.utcnow() + timedelta(days=30 * plan.months)
    nodes = await clicd_nodes(db)
    node = await node_for_instance(db, existing, nodes) if existing else find_clicd_node(nodes, plan.clicd_node)
    client = node.client()
    if existing and existing.clicd_id:
        status = await client.status(existing.clicd_id)
        if status != "running":
            await client.start(existing.clicd_id)
            status = "starting"
        existing.status, existing.last_synced_at = status, datetime.utcnow()
        if not (await db.execute(select(Job).where(Job.kind == "mail_instance", Job.ref_id == existing.id))).scalar_one_or_none():
            db.add(Job(kind="mail_instance", ref_id=existing.id))
        await db.commit()
        return
    order.status = "provisioning"
    await db.commit()
    resource_name = f"vps-{order.order_no.lower()}"
    created_response: dict = {}
    created = await client.find_by_name(resource_name)
    if not created:
        created_response = await client.create(plan_payload(plan, order.order_no, expires))
        created = unwrap_data(created_response)
    if not isinstance(created, dict):
        raise RuntimeError("CLICD 创建响应格式无效")
    instance_id = str(created.get("uuid") or created.get("id") or created.get("container_id") or "")
    if not instance_id:
        raise RuntimeError("CLICD 未返回实例 ID")
    detail_result = await client.get(instance_id)
    obj = unwrap_data(detail_result)
    if not isinstance(obj, dict):
        obj = created
    status = container_status(detail_result)
    if status != "running":
        await client.start(instance_id)
        status = "starting"
        for _ in range(5):
            await asyncio.sleep(1)
            status = await client.status(instance_id)
            if status == "running":
                break
    details = container_details(obj)
    credentials = extract_access(created_response) or extract_access(detail_result) or extract_access(created)
    if not credentials.get("username"):
        credentials = await client.create_sub_user(str(obj.get("name") or created.get("name") or resource_name))
    instance = existing or Instance(user_id=order.user_id, order_id=order.id, plan_id=plan.id, name=f"VPS-{order.order_no[-8:]}")
    instance.clicd_id = instance_id
    instance.clicd_node = node.base_url
    instance.status = status
    instance.ip = details.get("ip", "")
    instance.ipv6 = details.get("ipv6", "")
    instance.ssh_port = int(details.get("ssh_port") or 22)
    instance.ssh_password = encrypt(details["ssh_password"]) if details.get("ssh_password") else ""
    instance.management_url = credentials.get("management_url", "")
    instance.access_json = encrypted_access(credentials)
    instance.expires_at = expires
    instance.last_synced_at = datetime.utcnow()
    db.add(instance)
    order.status = "fulfilled"
    order.fulfilled_at = datetime.utcnow()
    await db.flush()
    if not (await db.execute(select(Job).where(Job.kind == "mail_instance", Job.ref_id == instance.id))).scalar_one_or_none():
        db.add(Job(kind="mail_instance", ref_id=instance.id))
    await db.commit()


@asynccontextmanager
async def lifespan(app):
    await init_db()
    task = asyncio.create_task(worker())
    yield
    task.cancel()


app = FastAPI(title="VPS-ONE", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=root / "static"), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
    return response


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db=Depends(session)):
    plans = (await db.execute(select(Plan).where(Plan.active.is_(True)).order_by(Plan.sort_order, Plan.price_cents))).scalars().all()
    site = {key: await get(db, key, default) for key, default in {"site_name": "VPS-ONE", "site_tagline": "高性能容器云", "site_footer": "稳定算力，专注增长"}.items()}
    return templates.TemplateResponse("home.html", ctx(request, plans=plans, site=site))


@app.get("/install", response_class=HTMLResponse)
async def install_page(request: Request, db=Depends(session)):
    if await db.scalar(select(func.count(User.id))):
        return RedirectResponse("/")
    return templates.TemplateResponse("install.html", {"request": request})


@app.post("/install")
async def install(email: str = Form(), password: str = Form(), db=Depends(session)):
    if len(password) < 12:
        raise HTTPException(400, "管理员密码至少 12 位")
    async with write_lock:
        if await db.scalar(select(func.count(User.id))):
            raise HTTPException(403)
        db.add(User(email=email.strip().lower(), password_hash=hash_password(password), is_admin=True))
        db.add_all([
            Plan(name="轻量云", slug="starter", description="开发与个人站点", price_cents=1999, cpu=1, memory_mb=1024, disk_gb=20, traffic_gb=500, network_down_mbps=100, network_up_mbps=50),
            Plan(name="标准云", slug="standard", description="企业应用首选", price_cents=3999, cpu=2, memory_mb=2048, disk_gb=40, traffic_gb=1000, network_down_mbps=200, network_up_mbps=100),
            Plan(name="性能云", slug="performance", description="高负载业务", price_cents=7999, cpu=4, memory_mb=4096, disk_gb=80, traffic_gb=2000, network_down_mbps=300, network_up_mbps=150),
        ])
        await db.commit()
    return RedirectResponse("/login", 303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
async def login(request: Request, email: str = Form(), password: str = Form(), db=Depends(session)):
    limit(request, "login", 8, 300)
    user = (await db.execute(select(User).where(User.email == email.strip().lower()))).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(user.password_hash, password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "邮箱或密码错误"}, status_code=400)
    user.last_login_at = datetime.utcnow()
    await db.commit()
    response = RedirectResponse("/admin" if user.is_admin else "/dashboard", 303)
    response.set_cookie("vps_session", session_token(user.id, user.is_admin), httponly=True, samesite="lax", secure=not cfg.debug, max_age=1209600)
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": ""})


@app.post("/register")
async def register(request: Request, email: str = Form(), password: str = Form(), db=Depends(session)):
    limit(request, "register", 5, 600)
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return templates.TemplateResponse("register.html", {"request": request, "error": "请输入有效邮箱"}, status_code=400)
    if len(password) < 10 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return templates.TemplateResponse("register.html", {"request": request, "error": "密码至少 10 位，且包含字母和数字"}, status_code=400)
    async with write_lock:
        if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
            return templates.TemplateResponse("register.html", {"request": request, "error": "邮箱已注册"}, status_code=409)
        db.add(User(email=email, password_hash=hash_password(password)))
        await db.commit()
    return RedirectResponse("/login", 303)


@app.post("/logout")
async def logout():
    response = RedirectResponse("/", 303)
    response.delete_cookie("vps_session")
    return response


@app.post("/orders")
async def create_order(request: Request, plan_id: int = Form(), csrf: str = Form(), db=Depends(session)):
    user = guard(request)
    check_csrf(request, csrf)
    limit(request, "order", 10, 300)
    plan = await db.get(Plan, plan_id)
    if not plan or not plan.active or plan.stock == 0:
        raise HTTPException(404, "套餐不可购买")
    order_no = "VP" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()
    order = Order(order_no=order_no, user_id=user["uid"], plan_id=plan.id, plan_snapshot=plan_snapshot(plan), amount_cents=plan.price_cents, currency=plan.currency)
    try:
        db.add(order)
        await db.commit()
        await db.refresh(order)
        base, merchant, private_key = await get(db, "hashpay_base_url"), await get(db, "hashpay_merchant_id"), await get(db, "hashpay_private_key")
        if not base or not merchant or not private_key:
            raise ValueError("HashPay 配置不完整")
        public_url = await site_url(db)
        result = await HashPay(base, merchant, private_key).create({"merchantNo": order_no, "amount": f"{plan.price_cents / 100:.2f}", "currency": plan.currency, "description": plan.name, "notify_url": public_url + "/hashpay/callback", "return_url": public_url + "/dashboard"})
        if not isinstance(result, dict):
            raise ValueError("HashPay 返回格式错误")
        nested = result.get("data") or result.get("order") or {}
        data = nested if isinstance(nested, dict) else {}
        order.hashpay_id = str(data.get("id") or data.get("orderId") or result.get("id") or result.get("orderId") or "") or None
        order.checkout_url = result.get("checkoutUrl") or result.get("payUrl") or data.get("checkoutUrl") or data.get("payUrl")
        if not order.checkout_url:
            raise ValueError("HashPay 未返回支付链接")
        order.status = "payment_pending"
        await db.commit()
    except Exception as exc:
        await db.rollback()
        try:
            persisted = await db.get(Order, order.id) if order.id else None
            if persisted:
                persisted.status = "payment_error"
                await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("订单 %s 支付失败状态保存失败", order_no)
        logger.exception("订单 %s 创建 HashPay 支付失败", order_no)
        raise HTTPException(502, f"支付订单创建失败：{exc}") from exc
    return RedirectResponse(order.checkout_url, 303)


@app.post("/hashpay/callback")
async def callback(request: Request, db=Depends(session)):
    limit(request, "callback", 120)
    raw = await request.json()
    private_key = await get(db, "hashpay_private_key")
    merchant_id = await get(db, "hashpay_merchant_id")
    if request.headers.get("X-HashPay-Merchant") != merchant_id:
        raise HTTPException(401, "HashPay 商户标识不匹配")
    try:
        payload = HashPay("", "", private_key).decrypt_callback(raw)
    except Exception as exc:
        raise HTTPException(400, "HashPay 回调解密失败") from exc
    order_no = str(payload.get("merchantNo") or "")
    event_id = str(payload.get("eventId") or payload.get("id") or secrets.token_hex(16))
    order = (await db.execute(select(Order).where(Order.order_no == order_no))).scalar_one_or_none()
    if not order:
        return JSONResponse({"error": "order not found"}, 404)
    async with write_lock:
        if (await db.execute(select(PaymentEvent).where(PaymentEvent.event_id == event_id))).scalar_one_or_none():
            return {"ok": True}
        amount = round(float(payload.get("amount", 0)) * 100)
        status = str(payload.get("status", "")).lower()
        verified = amount == order.amount_cents and status in {"paid", "success", "completed"}
        db.add(PaymentEvent(event_id=event_id, order_no=order_no, platform_txn_id=str(payload.get("transactionId") or ""), verified=verified, payload=json.dumps(payload, ensure_ascii=False)))
        if not verified:
            await db.commit()
            raise HTTPException(400, "支付数据不匹配")
        if order.status not in {"paid", "provisioning", "fulfilled"}:
            order.status, order.paid_at = "paid", datetime.utcnow()
            db.add(Job(kind="provision", ref_id=order.id))
        await db.commit()
    return {"ok": True}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db=Depends(session)):
    user = guard(request)
    orders = (await db.execute(select(Order).where(Order.user_id == user["uid"]).order_by(Order.id.desc()).limit(100))).scalars().all()
    instances = (await db.execute(select(Instance).where(Instance.user_id == user["uid"]).order_by(Instance.id.desc()).limit(100))).scalars().all()
    plans = {plan.id: plan for plan in (await db.execute(select(Plan).where(Plan.id.in_({order.plan_id for order in orders})))).scalars().all()} if orders else {}
    remote_by_id: dict[tuple[str, str], dict] = {}
    if instances:
        try:
            nodes = await clicd_nodes(db)
            remote_items, errors = await containers_by_node(nodes)
            for item in remote_items:
                for key in (item.get("uuid"), item.get("id"), item.get("container_id")):
                    if key not in {None, ""}:
                        remote_by_id[(item["_clicd_node"], str(key))] = item
            for instance in instances:
                node_url = instance.clicd_node or getattr(plans.get(instance.plan_id), "clicd_node", "")
                if not node_url:
                    matching_nodes = {key[0] for key in remote_by_id if key[1] == str(instance.clicd_id)}
                    if len(matching_nodes) == 1 and not errors:
                        node_url = matching_nodes.pop()
                    elif len(nodes) == 1:
                        node_url = nodes[0].base_url
                if node_url and not instance.clicd_node:
                    instance.clicd_node = node_url
                remote = remote_by_id.get((node_url, str(instance.clicd_id)))
                if remote:
                    details = container_details(remote)
                    instance.status = details["status"]
                    instance.ip, instance.ipv6 = details["ip"], details["ipv6"]
                    instance.ssh_port = details["ssh_port"]
                    if details["ssh_password"]:
                        instance.ssh_password = encrypt(details["ssh_password"])
                    instance.last_synced_at = datetime.utcnow()
            await db.commit()
            if errors:
                logger.warning("部分 CLICD 节点同步失败：%s", "; ".join(errors))
        except Exception as exc:
            logger.warning("容器列表同步失败：%s", exc)
    order_by_id = {order.id: order for order in orders}
    cards = [instance_card(instance, order_by_id[instance.order_id], plans[instance.plan_id], remote_by_id.get((instance.clicd_node, str(instance.clicd_id)))) for instance in instances if instance.order_id in order_by_id and instance.plan_id in plans]
    jobs = {job.ref_id: job for job in (await db.execute(select(Job).where(Job.kind == "provision", Job.ref_id.in_({order.id for order in orders})))).scalars().all()} if orders else {}
    return templates.TemplateResponse("dashboard.html", ctx(request, orders=orders, cards=cards, plans=plans, jobs=jobs, status_label=safe_status_label))


@app.get("/instances/{instance_id}/access")
async def instance_credentials(instance_id: int, request: Request, db=Depends(session)):
    user = guard(request)
    limit(request, "instance-access", 20, 300)
    instance = await db.get(Instance, instance_id)
    if not instance or instance.user_id != user["uid"]:
        raise HTTPException(404)
    credentials = instance_access(instance)
    db.add(Audit(user_id=user["uid"], action="instance.access.view", detail=str(instance_id), ip=request.client.host if request.client else ""))
    await db.commit()
    return JSONResponse({"instance": instance.name, "username": credentials.get("username", ""), "password": credentials.get("password", ""), "access_code": credentials.get("access_code", ""), "management_url": credentials.get("management_url", "")}, headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"})


@app.post("/instances/{instance_id}/actions/{action}")
async def instance_action(instance_id: int, action: str, request: Request, csrf: str = Form(), template_id: str = Form(""), db=Depends(session)):
    user = guard(request)
    check_csrf(request, csrf)
    limit(request, "instance", 20)
    instance = await db.get(Instance, instance_id)
    if not instance or instance.user_id != user["uid"]:
        raise HTTPException(404)
    allowed = {"start", "stop", "restart", "reset-password", "reinstall"}
    if action not in allowed:
        raise HTTPException(400, "不允许的操作")
    payload = {"template_id": template_id, "ssh_auth_mode": "keep"} if action == "reinstall" and template_id else {}
    client = (await node_for_instance(db, instance)).client()
    if action == "reset-password":
        new_password = secrets.token_urlsafe(15)
        confirmed_password = await client.reset_password(instance.clicd_id, new_password)
        remote = unwrap_data(await client.get(instance.clicd_id))
        details = container_details(remote)
        instance.ip = details.get("ip") or instance.ip
        instance.ipv6 = details.get("ipv6") or instance.ipv6
        instance.ssh_port = details.get("ssh_port") or instance.ssh_port
        instance.ssh_password = encrypt(confirmed_password)
        instance.last_synced_at = datetime.utcnow()
        order = await db.get(Order, instance.order_id)
        plan = await db.get(Plan, instance.plan_id)
        owner = await db.get(User, instance.user_id)
        card = instance_card(instance, order, plan, remote)
        card["ssh_password"] = confirmed_password
        db.add(Audit(user_id=user["uid"], action="instance.reset-password", detail=str(instance_id), ip=request.client.host if request.client else ""))
        await db.commit()
        try:
            await send_mail(db, owner.email, f"云主机 {instance.name} SSH 密码重置成功", instance_mail_text(card, order))
        except Exception as exc:
            logger.warning("实例 %s 密码已重置但邮件投递失败：%s", instance.id, exc.__class__.__name__)
            return RedirectResponse("/dashboard?mail_failed=1", 303)
        return RedirectResponse("/dashboard?password_reset=1", 303)
    await client.action(instance.clicd_id, action, payload)
    if action in {"start", "stop", "restart"}:
        instance.status = {"start": "starting", "stop": "stopping", "restart": "restarting"}[action]
        instance.last_synced_at = datetime.utcnow()
    db.add(Audit(user_id=user["uid"], action="instance." + action, detail=str(instance_id), ip=request.client.host if request.client else ""))
    await db.commit()
    return RedirectResponse("/dashboard", 303)


@app.post("/instances/{instance_id}/snapshot")
async def snapshot(instance_id: int, request: Request, csrf: str = Form(), name: str = Form(), db=Depends(session)):
    user = guard(request)
    check_csrf(request, csrf)
    instance = await db.get(Instance, instance_id)
    if not instance or instance.user_id != user["uid"]:
        raise HTTPException(404)
    await (await node_for_instance(db, instance)).client().create_snapshot(instance.clicd_id, name[:64])
    db.add(Audit(user_id=user["uid"], action="instance.snapshot", detail=name[:64]))
    await db.commit()
    return RedirectResponse("/dashboard", 303)


@app.post("/instances/{instance_id}/port")
async def add_port(instance_id: int, request: Request, csrf: str = Form(), protocol: str = Form(), host_port: int = Form(), container_port: int = Form(), description: str = Form(""), db=Depends(session)):
    user = guard(request)
    check_csrf(request, csrf)
    instance = await db.get(Instance, instance_id)
    if not instance or instance.user_id != user["uid"] or protocol not in {"tcp", "udp"} or not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
        raise HTTPException(400)
    await (await node_for_instance(db, instance)).client().add_port(instance.clicd_id, {"protocol": protocol, "host_port": host_port, "container_port": container_port, "description": description[:100]})
    db.add(Audit(user_id=user["uid"], action="instance.port.create", detail=f"{host_port}:{container_port}"))
    await db.commit()
    return RedirectResponse("/dashboard", 303)


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, db=Depends(session)):
    guard(request, True)
    models = {"用户": User, "套餐": Plan, "订单": Order, "实例": Instance, "任务": Job}
    stats = {name: await db.scalar(select(func.count(model.id))) for name, model in models.items()}
    orders = (await db.execute(select(Order).order_by(Order.id.desc()).limit(30))).scalars().all()
    jobs = (await db.execute(select(Job).order_by(Job.id.desc()).limit(30))).scalars().all()
    return templates.TemplateResponse("admin.html", ctx(request, stats=stats, orders=orders, jobs=jobs))


@app.get("/admin/products", response_class=HTMLResponse)
async def admin_products(request: Request, db=Depends(session)):
    guard(request, True)
    error = ""
    dashboard_data, containers, host, routing, tasks, security = {}, [], {}, {}, [], {}
    try:
        nodes = await clicd_nodes(db)
        containers, errors = await containers_by_node(nodes)
        overview = await asyncio.gather(*(asyncio.gather(node.client().dashboard(), node.client().host_info(), node.client().routing(), node.client().tasks(), node.client().security_summary()) for node in nodes), return_exceptions=True)
        host_nodes, routing_nodes, security_nodes = [], [], []
        for node, result in zip(nodes, overview):
            if isinstance(result, Exception):
                errors.append(f"{node.label}: {result}")
                continue
            _, node_host, node_routing, node_tasks, node_security = [unwrap(item) for item in result]
            host_nodes.append({"node": node.label, "data": node_host})
            routing_nodes.append({"node": node.label, "data": node_routing})
            security_nodes.append({"node": node.label, "data": node_security})
            if isinstance(node_tasks, list):
                tasks.extend({**item, "_clicd_node_label": node.label} if isinstance(item, dict) else item for item in node_tasks)
        statuses = [container_status(item) for item in containers]
        dashboard_data = {"total_containers": len(containers), "running": statuses.count("running"), "stopped": statuses.count("stopped")}
        host, routing, security = {"nodes": host_nodes}, {"nodes": routing_nodes}, {"nodes": security_nodes}
        error = "; ".join(errors)
    except Exception as exc:
        error = str(exc)
    audits = (await db.execute(select(Audit).where(Audit.action.like("admin.clicd.%")).order_by(Audit.id.desc()).limit(30))).scalars().all()
    return templates.TemplateResponse("admin_products.html", ctx(request, dashboard=dashboard_data or {}, containers=containers or [], host=host or {}, routing=routing or {}, tasks=tasks or [], security=security or {}, audits=audits, error=error))


@app.post("/admin/products/{container_ref}/actions/{action}")
async def admin_product_action(container_ref: str, action: str, request: Request, csrf: str = Form(), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    allowed = {"start", "stop", "restart", "reset-password"}
    if action not in allowed:
        raise HTTPException(400, "不允许的 CLICD 操作")
    node, container_id = await node_from_ref(db, container_ref)
    client = node.client()
    await client.action(container_id, action)
    db.add(Audit(user_id=user["uid"], action=f"admin.clicd.{action}", detail=f"{node.base_url} · {container_id}", ip=request.client.host if request.client else ""))
    await db.commit()
    return RedirectResponse("/admin/products", 303)


@app.post("/admin/products/{container_ref}/limits")
async def admin_product_limits(container_ref: str, request: Request, csrf: str = Form(), vcpu: int = Form(), ram_mb: int = Form(), network_down_mbps: int = Form(), network_up_mbps: int = Form(), io_read_mbps: int = Form(0), io_write_mbps: int = Form(0), monthly_traffic_gb: int = Form(0), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    if min(vcpu, ram_mb, network_down_mbps, network_up_mbps) < 0:
        raise HTTPException(400, "资源限制无效")
    node, container_id = await node_from_ref(db, container_ref)
    client = node.client()
    await client.update_resource_limit(container_id, {"vcpu": vcpu, "ram_mb": ram_mb, "network_down_mbps": network_down_mbps, "network_up_mbps": network_up_mbps, "io_read_mbps": io_read_mbps, "io_write_mbps": io_write_mbps})
    await client.update_traffic_limit(container_id, {"traffic_mode": "total", "monthly_traffic_gb": monthly_traffic_gb})
    db.add(Audit(user_id=user["uid"], action="admin.clicd.limits", detail=f"{node.base_url} · {container_id}"))
    await db.commit()
    return RedirectResponse("/admin/products", 303)


@app.post("/admin/products/{container_ref}/delete")
async def admin_product_delete(container_ref: str, request: Request, csrf: str = Form(), confirmation: str = Form(), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    node, container_id = await node_from_ref(db, container_ref)
    if confirmation != container_id:
        raise HTTPException(400, "请输入完整容器 ID 确认删除")
    await node.client().delete(container_id)
    db.add(Audit(user_id=user["uid"], action="admin.clicd.delete", detail=f"{node.base_url} · {container_id}", ip=request.client.host if request.client else ""))
    await db.commit()
    return RedirectResponse("/admin/products", 303)


@app.get("/admin/plans", response_class=HTMLResponse)
async def admin_plans(request: Request, db=Depends(session)):
    guard(request, True)
    plans = (await db.execute(select(Plan).order_by(Plan.sort_order, Plan.id))).scalars().all()
    templates_list, error = [], ""
    try:
        nodes = await clicd_nodes(db)
        results = await asyncio.gather(*(node.client().templates() for node in nodes), return_exceptions=True)
        errors = []
        for node, result in zip(nodes, results):
            if isinstance(result, Exception):
                errors.append(f"{node.label}: {result}")
                continue
            for image in unwrap(result) or []:
                image_id = str(image.get("id") or image.get("template_id") or image.get("slug") or "")
                if image_id:
                    templates_list.append({**image, "_clicd_node": node.base_url, "_clicd_node_label": node.label, "_clicd_choice": plan_image_choice(node, image_id)})
        error = "; ".join(errors)
    except Exception as exc:
        error = str(exc)
    return templates.TemplateResponse("admin_plans.html", ctx(request, plans=plans, templates_list=templates_list, error=error))


@app.post("/admin/plans")
async def save_plan(request: Request, csrf: str = Form(), plan_id: int = Form(0), name: str = Form(), slug: str = Form(), description: str = Form(""), price_cents: int = Form(), months: int = Form(1), stock: int = Form(-1), cpu: int = Form(), memory_mb: int = Form(), disk_gb: int = Form(), traffic_gb: int = Form(), network_down_mbps: int = Form(), network_up_mbps: int = Form(), virtualization: str = Form("lxc"), clicd_image: str = Form(), assign_nat: bool = Form(False), assign_ipv4: bool = Form(False), assign_ipv6: bool = Form(False), active: bool = Form(False), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    if virtualization not in {"lxc", "kvm"} or min(price_cents, months, cpu, memory_mb, disk_gb) < 1:
        raise HTTPException(400, "套餐字段无效")
    selected_node, image_id = parse_plan_image_choice(clicd_image)
    node = find_clicd_node(await clicd_nodes(db), selected_node)
    client = node.client()
    images = unwrap(await client.templates(virtualization)) or []
    matched = next((item for item in images if str(item.get("id") or item.get("template_id") or item.get("slug")) == image_id), None)
    if not matched:
        raise HTTPException(400, "CLICD 中未找到已启用且已下载的对应镜像")
    plan = await db.get(Plan, plan_id) if plan_id else Plan(name=name, slug=slug, price_cents=price_cents, cpu=cpu, memory_mb=memory_mb, disk_gb=disk_gb)
    for key, value in {"name": name, "slug": slug, "description": description, "price_cents": price_cents, "months": months, "stock": stock, "cpu": cpu, "memory_mb": memory_mb, "disk_gb": disk_gb, "traffic_gb": traffic_gb, "network_down_mbps": network_down_mbps, "network_up_mbps": network_up_mbps, "virtualization": virtualization, "clicd_node": node.base_url, "clicd_image": image_id, "clicd_template_name": str(matched.get("name") or matched.get("label") or image_id), "clicd_validated_at": datetime.utcnow(), "assign_nat": assign_nat, "assign_ipv4": assign_ipv4, "assign_ipv6": assign_ipv6, "active": active}.items():
        setattr(plan, key, value)
    db.add(plan)
    db.add(Audit(user_id=user["uid"], action="plan.save", detail=slug))
    await db.commit()
    return RedirectResponse("/admin/plans", 303)


@app.post("/admin/plans/{plan_id}/toggle")
async def toggle_plan(plan_id: int, request: Request, csrf: str = Form(), db=Depends(session)):
    guard(request, True)
    check_csrf(request, csrf)
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404)
    plan.active = not plan.active
    await db.commit()
    return RedirectResponse("/admin/plans", 303)


@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db=Depends(session)):
    guard(request, True)
    keys = ["site_name", "site_tagline", "site_footer", "site_url", "clicd_base_url", "hashpay_base_url", "hashpay_merchant_id", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_from"]
    values = {key: await get(db, key) for key in keys}
    return templates.TemplateResponse("settings.html", ctx(request, values=values, clicd_count=len(setting_lines(values["clicd_base_url"])), saved=request.query_params.get("saved")))


@app.post("/admin/settings")
async def settings_save(request: Request, csrf: str = Form(), db=Depends(session)):
    user = guard(request, True)
    form = await request.form()
    check_csrf(request, csrf)
    allowed = {"site_name", "site_tagline", "site_footer", "site_url", "clicd_base_url", "clicd_token", "hashpay_base_url", "hashpay_merchant_id", "hashpay_private_key", "hashpay_public_key", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "smtp_from"}
    values = {key: str(value).strip() for key, value in form.items() if key in allowed}
    for key in {"site_url", "hashpay_base_url"} & values.keys():
        parsed = urlparse(values[key])
        if values[key] and parsed.scheme not in {"http", "https"}:
            raise HTTPException(400, "接口地址必须使用 HTTP 或 HTTPS")
    effective_urls = values.get("clicd_base_url", await get(db, "clicd_base_url"))
    effective_tokens = values.get("clicd_token") or await get(db, "clicd_token")
    try:
        parse_clicd_nodes(effective_urls, effective_tokens)
    except CLICDError as exc:
        raise HTTPException(400, str(exc)) from exc
    secret_keys = {"clicd_token", "hashpay_private_key", "hashpay_public_key", "smtp_password"}
    await set_many(db, values, secret_keys)
    db.add(Audit(user_id=user["uid"], action="settings.update"))
    await db.commit()
    return RedirectResponse("/admin/settings?saved=1", 303)


@app.post("/admin/settings/test/{service}")
async def test_service(service: str, request: Request, csrf: str = Form(), recipient: str = Form(""), db=Depends(session)):
    guard(request, True)
    check_csrf(request, csrf)
    if service == "clicd":
        nodes = await clicd_nodes(db)
        await asyncio.gather(*(node.client().test() for node in nodes))
    elif service == "smtp":
        await send_mail(db, recipient, "VPS-ONE SMTP 测试", "邮件配置工作正常。")
    elif service == "hashpay":
        base = await get(db, "hashpay_base_url")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(base)
            response.raise_for_status()
    else:
        raise HTTPException(404)
    return RedirectResponse("/admin/settings?saved=test-ok", 303)
