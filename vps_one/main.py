import asyncio
import base64
import binascii
import hashlib
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
from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from websockets.asyncio.client import connect as websocket_connect

from .config import settings
from .database import SessionLocal, init_db, session, write_lock
from .models import Audit, CardItem, Instance, Job, Order, PaymentEvent, Plan, RefundRequest, User, WalletEntry, WalletTopUp
from .security import confirmation_code, confirmation_hash, csrf_token, decrypt, encrypt, hash_password, read_session, session_token, valid_confirmation, valid_csrf, verify_password
from .services.accounts import ensure_wallet, generate_login_name, parse_money_cents, payment_amount_cents, post_wallet_entry, refund_deadline, refund_eligible
from .services.cards import import_card_items, reserve_card_item, reveal_card_secret
from .services.clicd import CLICD, CLICDError, CLICD_USER_AGENT, container_details, container_items, container_status, extract_access, normalize_virtualization, plan_payload, unwrap_data
from .services.hashpay import HashPay
from .services.mailer import MailDeliveryError, send_mail
from .services.settings import get, set_many

cfg = settings()
root = Path(__file__).parent
templates = Jinja2Templates(root / "templates")
rate_buckets: dict[str, list[float]] = {}
logger = logging.getLogger("vps_one.worker")
VNC_SESSION_TTL = 55
REFUND_CONFIRMATION_TTL = timedelta(minutes=15)
REFUND_MAX_REQUESTS_24H = 5
REFUND_ACTIVE_STATUSES = {"confirmation_pending", "confirmation_locked", "email_failed", "expired", "pending_review", "approved", "processing", "processing_failed"}


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


@dataclass(frozen=True)
class VNCSession:
    user_id: int
    instance_id: int
    node_url: str
    container_name: str
    clicd_ticket: str
    expires_at: float


vnc_sessions: dict[str, VNCSession] = {}


def create_vnc_session(user_id: int, instance_id: int, node_url: str, container_name: str, clicd_ticket: str) -> str:
    now = time.monotonic()
    for token, value in list(vnc_sessions.items()):
        if value.expires_at <= now:
            vnc_sessions.pop(token, None)
    token = secrets.token_urlsafe(32)
    vnc_sessions[token] = VNCSession(user_id, instance_id, node_url, container_name, clicd_ticket, now + VNC_SESSION_TTL)
    return token


def consume_vnc_session(token: str, user_id: int, instance_id: int) -> VNCSession | None:
    value = vnc_sessions.pop(token, None)
    if not value or value.expires_at <= time.monotonic():
        return None
    return value if value.user_id == user_id and value.instance_id == instance_id else None


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


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = urlparse(websocket.headers.get("origin", ""))
    host = (websocket.headers.get("x-forwarded-host") or websocket.headers.get("host") or "").split(",", 1)[0].strip().lower()
    return origin.scheme in {"http", "https"} and origin.netloc.lower() == host


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
    fields = ["name", "description", "product_type", "card_delivery_note", "price_cents", "currency", "months", "cpu", "memory_mb", "disk_gb", "traffic_gb", "network_down_mbps", "network_up_mbps", "virtualization", "clicd_node", "clicd_image", "assign_nat", "port_mapping_count"]
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


def order_status_label(status: str) -> str:
    return {"pending": "待支付", "payment_pending": "待支付", "payment_error": "支付失败", "paid": "已支付", "provisioning": "交付中", "delivering": "发卡中", "delivery_failed": "发卡异常", "fulfilled": "已完成", "refunded": "已退款"}.get(status, status)


def refund_status_label(status: str) -> str:
    return {
        "confirmation_pending": "待邮箱确认", "confirmation_locked": "验证码已锁定", "email_failed": "邮件发送失败",
        "pending_review": "待后台审核", "approved": "审核通过", "processing": "退款处理中",
        "processing_failed": "处理失败", "rejected": "审核拒绝", "completed": "已退款", "expired": "已过期",
    }.get(status, status)


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
    return value if isinstance(value, dict) and value else {field: getattr(plan, field, "") for field in ("name", "product_type", "card_delivery_note", "cpu", "memory_mb", "disk_gb", "traffic_gb", "network_down_mbps", "network_up_mbps", "clicd_image", "assign_nat", "port_mapping_count")}


def order_job_kind(order: Order) -> str:
    return "deliver_card" if (order.product_type or "cloud") == "card" else "provision"


def instance_card(instance: Instance, order: Order, plan: Plan, remote: dict | None = None) -> dict:
    details = container_details(remote or {})
    access = instance_access(instance)
    package = snapshot_data(order, plan)
    virtualization = details.get("virtualization") or normalize_virtualization(package.get("virtualization")) or normalize_virtualization(plan.virtualization)
    return {
        "instance": instance,
        "order": order,
        "package": package,
        "operating_system": details.get("operating_system") or package.get("clicd_image") or "未返回",
        "virtualization": virtualization,
        "is_kvm": virtualization == "kvm",
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


async def hashpay_checkout(db, merchant_no: str, amount_cents: int, currency: str, description: str, return_path: str) -> tuple[str, str | None]:
    base = await get(db, "hashpay_base_url")
    merchant = await get(db, "hashpay_merchant_id")
    private_key = await get(db, "hashpay_private_key")
    if not base or not merchant or not private_key:
        raise ValueError("HashPay 配置不完整")
    public_url = await site_url(db)
    result = await HashPay(base, merchant, private_key).create({
        "merchantNo": merchant_no,
        "amount": f"{amount_cents / 100:.2f}",
        "currency": currency,
        "description": description,
        "notify_url": public_url + "/hashpay/callback",
        "return_url": public_url + return_path,
    })
    if not isinstance(result, dict):
        raise ValueError("HashPay 返回格式错误")
    nested = result.get("data") or result.get("order") or {}
    data = nested if isinstance(nested, dict) else {}
    checkout_url = result.get("checkoutUrl") or result.get("payUrl") or data.get("checkoutUrl") or data.get("payUrl")
    if not checkout_url:
        raise ValueError("HashPay 未返回支付链接")
    hashpay_id = str(data.get("id") or data.get("orderId") or result.get("id") or result.get("orderId") or "") or None
    return str(checkout_url), hashpay_id


async def send_refund_confirmation(db, refund: RefundRequest, user: User, order: Order, code: str) -> None:
    expiry = refund.confirmation_expires_at.strftime("%Y-%m-%d %H:%M UTC") if refund.confirmation_expires_at else "15 分钟后"
    await send_mail(
        db,
        user.email,
        f"订单 {order.order_no} 撤销确认码",
        f"您正在申请撤销已完成订单 {order.order_no}。\n\n确认码：{code}\n退款金额：{order.currency} {order.amount_cents / 100:.2f}\n确认码有效期：{expiry}\n\n确认后申请将进入人工审核。审核通过后，对应 CLICD 容器会被销毁，费用退入与 {user.email} 关联的钱包。若非本人操作，请忽略此邮件并及时修改密码。",
    )


async def send_card_credentials(db, order: Order, user: User, plan: Plan, item: CardItem) -> None:
    package = snapshot_data(order, plan)
    note = str(package.get("card_delivery_note") or plan.card_delivery_note or "").strip()
    note_section = f"\n使用说明：{note}\n" if note else "\n"
    await send_mail(
        db,
        user.email,
        f"订单 {order.order_no} 卡密已交付",
        f"您购买的数字商品已完成交付。\n\n商品：{package.get('name') or plan.name}\n订单：{order.order_no}\n卡密：{reveal_card_secret(item)}\n{note_section}\n卡密全文仅发送至注册邮箱，请妥善保管并尽快使用。",
    )


async def process_card_delivery(db, order_id: int, resend: bool = False) -> None:
    order = await db.get(Order, order_id)
    if not order or order.product_type != "card":
        raise RuntimeError("发卡任务对应的订单无效")
    if resend and order.status != "fulfilled":
        raise RuntimeError("仅已交付订单可以重新发送卡密")
    if not resend and order.status == "fulfilled":
        return
    if not resend and order.status not in {"paid", "delivering", "delivery_failed"}:
        raise RuntimeError("发卡订单当前状态不可交付")
    plan = await db.get(Plan, order.plan_id)
    user = await db.get(User, order.user_id)
    if not plan or plan.product_type != "card" or not user:
        raise RuntimeError("发卡任务缺少用户或套餐数据")
    try:
        async with write_lock:
            if resend:
                item = (await db.execute(select(CardItem).where(CardItem.order_id == order.id))).scalar_one_or_none()
                if not item:
                    raise RuntimeError("订单缺少已分配卡密")
            else:
                item = await reserve_card_item(db, order, plan)
            item.email_attempts += 1
            item.error = ""
            if not resend:
                order.status = "delivering"
            await db.commit()
        await send_card_credentials(db, order, user, plan, item)
        async with write_lock:
            await db.refresh(item)
            await db.refresh(order)
            now = datetime.utcnow()
            item.status = "delivered"
            item.delivered_at = item.delivered_at or now
            item.email_sent_at = now
            item.error = ""
            if not resend:
                order.status = "fulfilled"
                order.fulfilled_at = order.fulfilled_at or now
            db.add(Audit(user_id=user.id, action="card.email.resent" if resend else "card.delivered", detail=f"{order.order_no} · {item.masked_value}"))
            await db.commit()
    except Exception as exc:
        await db.rollback()
        failed_order = await db.get(Order, order_id)
        failed_item = (await db.execute(select(CardItem).where(CardItem.order_id == order_id))).scalar_one_or_none()
        if failed_item:
            failed_item.error = str(exc)[:1000]
        if failed_order and not resend and failed_order.status != "fulfilled":
            failed_order.status = "delivery_failed"
        await db.commit()
        raise


async def process_refund(db, refund_id: int):
    refund = await db.get(RefundRequest, refund_id)
    if not refund or refund.status == "completed" or refund.status not in {"approved", "processing", "processing_failed"}:
        return
    try:
        order = await db.get(Order, refund.order_id)
        instance = (await db.execute(select(Instance).where(Instance.order_id == refund.order_id))).scalar_one_or_none()
        if not order or order.user_id != refund.user_id or order.status != "fulfilled":
            raise RuntimeError("退款申请与可退订单不匹配")
        if order.amount_cents != refund.amount_cents or order.currency != refund.currency:
            raise RuntimeError("退款申请金额或币种与订单不匹配")
        if not instance or instance.user_id != refund.user_id or not instance.clicd_id:
            raise RuntimeError("退款订单缺少可销毁的 CLICD 实例")
        refund.status, refund.error = "processing", ""
        await db.commit()
        if not refund.container_deleted_at:
            client = (await node_for_instance(db, instance)).client()
            try:
                await client.delete(instance.clicd_id)
            except CLICDError as exc:
                if "404" not in str(exc):
                    raise
            refund.container_deleted_at = datetime.utcnow()
            instance.status = "deleted"
            await db.commit()
        async with write_lock:
            await db.refresh(refund)
            wallet = await ensure_wallet(db, refund.user_id, refund.currency)
            if wallet.currency != refund.currency:
                raise RuntimeError("钱包币种与退款币种不一致")
            await post_wallet_entry(db, wallet, refund.amount_cents, "refund", "refund", refund.id, f"订单 {order.order_no} 撤销退款")
            now = datetime.utcnow()
            refund.status = "completed"
            refund.refunded_at = refund.refunded_at or now
            refund.completed_at = now
            refund.error = ""
            order.status = "refunded"
            db.add(Audit(user_id=refund.reviewed_by, action="refund.completed", detail=f"{refund.refund_no} · {order.order_no}"))
            await db.commit()
    except Exception as exc:
        await db.rollback()
        failed = await db.get(RefundRequest, refund_id)
        if failed and failed.status != "completed":
            failed.status = "processing_failed"
            failed.error = str(exc)[:1000]
            await db.commit()
        raise


async def process_job(db, job: Job):
    if job.kind == "provision":
        await provision(db, job.ref_id)
    elif job.kind == "deliver_card":
        await process_card_delivery(db, job.ref_id)
    elif job.kind == "mail_card":
        await process_card_delivery(db, job.ref_id, resend=True)
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
    elif job.kind == "refund":
        await process_refund(db, job.ref_id)


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
    if not order or order.product_type != "cloud" or order.status not in {"paid", "provisioning"}:
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
    signed_in = current(request)
    account_user = await db.get(User, signed_in["uid"]) if signed_in else None
    wallet = await ensure_wallet(db, account_user.id) if account_user else None
    if account_user:
        await db.commit()
    return templates.TemplateResponse("home.html", ctx(request, plans=plans, site=site, account_user=account_user, wallet=wallet))


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
        admin_user = User(username=generate_login_name(), email=email.strip().lower(), password_hash=hash_password(password), is_admin=True)
        db.add(admin_user)
        await db.flush()
        await ensure_wallet(db, admin_user.id)
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
        existing_names = set((await db.execute(select(User.username))).scalars().all())
        registered_user = User(username=generate_login_name(existing_names), email=email, password_hash=hash_password(password))
        db.add(registered_user)
        await db.flush()
        await ensure_wallet(db, registered_user.id)
        await db.commit()
    return RedirectResponse("/login", 303)


@app.post("/logout")
async def logout():
    response = RedirectResponse("/", 303)
    response.delete_cookie("vps_session")
    return response


@app.post("/orders")
async def create_order(request: Request, plan_id: int = Form(), csrf: str = Form(), payment_method: str = Form("hashpay"), db=Depends(session)):
    user = guard(request)
    check_csrf(request, csrf)
    limit(request, "order", 10, 300)
    plan = await db.get(Plan, plan_id)
    if not plan or not plan.active or plan.stock == 0 or payment_method not in {"hashpay", "wallet"}:
        raise HTTPException(404, "套餐不可购买")
    order_no = "VP" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()
    order = Order(order_no=order_no, user_id=user["uid"], plan_id=plan.id, plan_snapshot=plan_snapshot(plan), amount_cents=plan.price_cents, currency=plan.currency, product_type=plan.product_type, payment_method=payment_method)
    if payment_method == "wallet":
        try:
            async with write_lock:
                wallet = await ensure_wallet(db, user["uid"], plan.currency)
                if wallet.currency != plan.currency:
                    raise ValueError("钱包币种与套餐币种不一致")
                db.add(order)
                await db.flush()
                if order.product_type == "card":
                    await reserve_card_item(db, order, plan)
                await post_wallet_entry(db, wallet, -plan.price_cents, "purchase", "order", order.id, f"购买套餐 {plan.name}")
                order.status, order.paid_at = "paid", datetime.utcnow()
                db.add(Job(kind=order_job_kind(order), ref_id=order.id))
                db.add(Audit(user_id=user["uid"], action="wallet.purchase", detail=order_no, ip=request.client.host if request.client else ""))
                await db.commit()
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(409, str(exc)) from exc
        return RedirectResponse("/account?card_purchase=1#orders" if order.product_type == "card" else "/dashboard?wallet_purchase=1", 303)
    order_id: int | None = None
    try:
        db.add(order)
        await db.commit()
        await db.refresh(order)
        order_id = order.id
        return_path = "/account?payment=returned#orders" if order.product_type == "card" else "/dashboard"
        checkout_url, order.hashpay_id = await hashpay_checkout(db, order_no, plan.price_cents, plan.currency, plan.name, return_path)
        order.checkout_url = checkout_url
        order.status = "payment_pending"
        await db.commit()
    except Exception as exc:
        await db.rollback()
        try:
            persisted = await db.get(Order, order_id) if order_id else None
            if persisted:
                persisted.status = "payment_error"
                await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("订单 %s 支付失败状态保存失败", order_no)
        logger.exception("订单 %s 创建 HashPay 支付失败", order_no)
        raise HTTPException(502, f"支付订单创建失败：{exc}") from exc
    return RedirectResponse(checkout_url, 303)


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
    merchant_no = str(payload.get("merchantNo") or "")
    event_id = str(payload.get("eventId") or payload.get("id") or hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
    order = (await db.execute(select(Order).where(Order.order_no == merchant_no))).scalar_one_or_none()
    topup = (await db.execute(select(WalletTopUp).where(WalletTopUp.topup_no == merchant_no))).scalar_one_or_none() if not order else None
    target = order or topup
    if not target:
        return JSONResponse({"error": "payment reference not found"}, 404)
    async with write_lock:
        if (await db.execute(select(PaymentEvent).where(PaymentEvent.event_id == event_id))).scalar_one_or_none():
            return {"ok": True}
        try:
            amount = payment_amount_cents(payload.get("amount", 0))
        except ValueError:
            amount = -1
        status = str(payload.get("status", "")).lower()
        currency_matches = str(payload.get("currency") or target.currency).upper() == target.currency.upper()
        verified = amount == target.amount_cents and currency_matches and status in {"paid", "success", "completed"}
        db.add(PaymentEvent(event_id=event_id, order_no=merchant_no, platform_txn_id=str(payload.get("transactionId") or ""), verified=verified, payload=json.dumps(payload, ensure_ascii=False)))
        if not verified:
            await db.commit()
            raise HTTPException(400, "支付数据不匹配")
        if order and order.status in {"pending", "payment_pending", "payment_error"}:
            order.status, order.paid_at = "paid", datetime.utcnow()
            if order.product_type == "card":
                plan = await db.get(Plan, order.plan_id)
                if not plan or plan.product_type != "card":
                    raise HTTPException(409, "发卡套餐不存在或类型不匹配")
                try:
                    await reserve_card_item(db, order, plan)
                except ValueError as exc:
                    order.status = "delivery_failed"
                    db.add(Audit(user_id=order.user_id, action="card.stock.shortage", detail=f"{order.order_no} · {exc}"))
            db.add(Job(kind=order_job_kind(order), ref_id=order.id))
        elif topup and topup.status != "paid":
            wallet = await ensure_wallet(db, topup.user_id, topup.currency)
            await post_wallet_entry(db, wallet, topup.amount_cents, "topup", "topup", topup.id, f"钱包充值 {topup.topup_no}")
            topup.status, topup.paid_at = "paid", datetime.utcnow()
        await db.commit()
    return {"ok": True}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db=Depends(session)):
    user = guard(request)
    account_user = await db.get(User, user["uid"])
    orders = (await db.execute(select(Order).where(Order.user_id == user["uid"]).order_by(Order.id.desc()).limit(100))).scalars().all()
    instances = (await db.execute(select(Instance).where(Instance.user_id == user["uid"], Instance.status != "deleted").order_by(Instance.id.desc()).limit(100))).scalars().all()
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
    cards = [instance_card(instance, order_by_id[instance.order_id], plans[instance.plan_id], remote_by_id.get((instance.clicd_node, str(instance.clicd_id)))) for instance in instances if instance.order_id in order_by_id and instance.plan_id in plans and order_by_id[instance.order_id].status != "refunded"]
    jobs = {job.ref_id: job for job in (await db.execute(select(Job).where(Job.kind.in_({"provision", "deliver_card"}), Job.ref_id.in_({order.id for order in orders})))).scalars().all()} if orders else {}
    return templates.TemplateResponse("dashboard.html", ctx(request, account_user=account_user, orders=orders, cards=cards, plans=plans, jobs=jobs, status_label=safe_status_label, order_status_label=order_status_label))


@app.get("/account", response_class=HTMLResponse)
async def account(request: Request, db=Depends(session)):
    session_user = guard(request)
    account_user = await db.get(User, session_user["uid"])
    wallet = await ensure_wallet(db, account_user.id)
    await db.commit()
    orders = (await db.execute(select(Order).where(Order.user_id == account_user.id).order_by(Order.id.desc()).limit(100))).scalars().all()
    entries = (await db.execute(select(WalletEntry).where(WalletEntry.wallet_id == wallet.id).order_by(WalletEntry.id.desc()).limit(30))).scalars().all()
    topups = (await db.execute(select(WalletTopUp).where(WalletTopUp.user_id == account_user.id).order_by(WalletTopUp.id.desc()).limit(20))).scalars().all()
    refunds = (await db.execute(select(RefundRequest).where(RefundRequest.user_id == account_user.id).order_by(RefundRequest.id.desc()).limit(100))).scalars().all()
    latest_refunds: dict[int, RefundRequest] = {}
    for refund in refunds:
        latest_refunds.setdefault(refund.order_id, refund)
    plans = {plan.id: plan for plan in (await db.execute(select(Plan).where(Plan.id.in_({order.plan_id for order in orders})))).scalars().all()} if orders else {}
    instances = {instance.order_id: instance for instance in (await db.execute(select(Instance).where(Instance.user_id == account_user.id))).scalars().all()}
    card_items = {item.order_id: item for item in (await db.execute(select(CardItem).where(CardItem.order_id.in_({order.id for order in orders})))).scalars().all()} if orders else {}
    recent_request_count = await db.scalar(select(func.count(RefundRequest.id)).where(RefundRequest.user_id == account_user.id, RefundRequest.requested_at >= datetime.utcnow() - timedelta(hours=24))) or 0
    order_rows = []
    for order in orders:
        refund = latest_refunds.get(order.id)
        blocked = bool(refund and (refund.status in REFUND_ACTIVE_STATUSES or refund.status == "completed"))
        order_rows.append({
            "order": order,
            "package": snapshot_data(order, plans.get(order.plan_id)) if plans.get(order.plan_id) else {},
            "instance": instances.get(order.id),
            "card_item": card_items.get(order.id),
            "refund": refund,
            "refund_deadline": refund_deadline(order),
            "can_refund": refund_eligible(order) and not blocked and recent_request_count < REFUND_MAX_REQUESTS_24H,
        })
    return templates.TemplateResponse("account.html", ctx(
        request,
        account_user=account_user,
        wallet=wallet,
        entries=entries,
        topups=topups,
        order_rows=order_rows,
        refund_requests_remaining=max(0, REFUND_MAX_REQUESTS_24H - recent_request_count),
        order_status_label=order_status_label,
        refund_status_label=refund_status_label,
    ))


@app.post("/account/orders/{order_id}/card-email")
async def resend_card_email(order_id: int, request: Request, csrf: str = Form(), db=Depends(session)):
    session_user = guard(request)
    check_csrf(request, csrf)
    limit(request, f"card-email:{session_user['uid']}", 3, 3600)
    async with write_lock:
        order = await db.get(Order, order_id)
        if not order or order.user_id != session_user["uid"]:
            raise HTTPException(404)
        if order.product_type != "card" or order.status != "fulfilled":
            raise HTTPException(409, "该订单当前不能重新发送卡密")
        item = (await db.execute(select(CardItem).where(CardItem.order_id == order.id))).scalar_one_or_none()
        if not item:
            raise HTTPException(409, "订单缺少已交付卡密")
        job = (await db.execute(select(Job).where(Job.kind == "mail_card", Job.ref_id == order.id))).scalar_one_or_none()
        if not job:
            db.add(Job(kind="mail_card", ref_id=order.id))
        else:
            job.status, job.attempts, job.error, job.locked_at, job.run_after = "pending", 0, "", None, datetime.utcnow()
        db.add(Audit(user_id=session_user["uid"], action="card.email.request", detail=f"{order.order_no} · {item.masked_value}", ip=request.client.host if request.client else ""))
        await db.commit()
    return RedirectResponse("/account?card_mail=queued#orders", 303)


@app.post("/account/wallet/topups")
async def create_wallet_topup(request: Request, csrf: str = Form(), amount: str = Form(), db=Depends(session)):
    session_user = guard(request)
    check_csrf(request, csrf)
    limit(request, "wallet-topup", 10, 600)
    try:
        amount_cents = parse_money_cents(amount)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    account_user = await db.get(User, session_user["uid"])
    wallet = await ensure_wallet(db, account_user.id)
    topup_no = "WU" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()
    topup = WalletTopUp(topup_no=topup_no, user_id=account_user.id, wallet_id=wallet.id, amount_cents=amount_cents, currency=wallet.currency)
    db.add(topup)
    await db.commit()
    await db.refresh(topup)
    topup_id = topup.id
    try:
        checkout_url, topup.hashpay_id = await hashpay_checkout(db, topup_no, amount_cents, wallet.currency, f"{account_user.username} 钱包充值", "/account?topup=returned")
        topup.checkout_url = checkout_url
        topup.status = "payment_pending"
        db.add(Audit(user_id=account_user.id, action="wallet.topup.create", detail=topup_no, ip=request.client.host if request.client else ""))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        persisted = await db.get(WalletTopUp, topup_id)
        if persisted:
            persisted.status = "payment_error"
            await db.commit()
        logger.exception("充值单 %s 创建 HashPay 支付失败", topup_no)
        raise HTTPException(502, f"充值支付创建失败：{exc}") from exc
    return RedirectResponse(checkout_url, 303)


@app.post("/account/orders/{order_id}/refunds")
async def request_refund(order_id: int, request: Request, csrf: str = Form(), reason: str = Form(""), db=Depends(session)):
    session_user = guard(request)
    check_csrf(request, csrf)
    limit(request, f"refund-request:{session_user['uid']}", REFUND_MAX_REQUESTS_24H, 86400)
    async with write_lock:
        order = await db.get(Order, order_id)
        if not order or order.user_id != session_user["uid"]:
            raise HTTPException(404)
        if order.product_type != "cloud":
            raise HTTPException(409, "数字卡密商品交付后不支持自助撤销")
        if not refund_eligible(order):
            raise HTTPException(409, "该订单不在支付后 24 小时撤销窗口内")
        account_user = await db.get(User, session_user["uid"])
        recent_count = await db.scalar(select(func.count(RefundRequest.id)).where(RefundRequest.user_id == account_user.id, RefundRequest.requested_at >= datetime.utcnow() - timedelta(hours=24))) or 0
        if recent_count >= REFUND_MAX_REQUESTS_24H:
            raise HTTPException(429, "24 小时内最多提交 5 次撤销申请")
        existing = (await db.execute(select(RefundRequest).where(RefundRequest.order_id == order.id).order_by(RefundRequest.id.desc()))).scalars().first()
        if existing and (existing.status in REFUND_ACTIVE_STATUSES or existing.status == "completed"):
            raise HTTPException(409, "该订单已有正在处理或已完成的撤销申请")
        code = confirmation_code()
        refund_no = "RF" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()
        refund = RefundRequest(
            refund_no=refund_no,
            order_id=order.id,
            user_id=account_user.id,
            amount_cents=order.amount_cents,
            currency=order.currency,
            reason=reason.strip()[:500],
            confirmation_hash=confirmation_hash(refund_no, code),
            confirmation_expires_at=datetime.utcnow() + REFUND_CONFIRMATION_TTL,
            email_attempts=1,
        )
        db.add(refund)
        db.add(Audit(user_id=account_user.id, action="refund.request", detail=f"{refund_no} · {order.order_no}", ip=request.client.host if request.client else ""))
        await db.commit()
    try:
        await send_refund_confirmation(db, refund, account_user, order, code)
        return RedirectResponse("/account?refund=code-sent#orders", 303)
    except MailDeliveryError as exc:
        async with write_lock:
            await db.refresh(refund)
            if refund.status == "confirmation_pending":
                refund.status, refund.error = "email_failed", str(exc)[:1000]
                await db.commit()
        return RedirectResponse("/account?refund=mail-failed#orders", 303)


@app.post("/account/refunds/{refund_id}/confirm")
async def confirm_refund(refund_id: int, request: Request, csrf: str = Form(), code: str = Form(), db=Depends(session)):
    session_user = guard(request)
    check_csrf(request, csrf)
    limit(request, f"refund-confirm:{refund_id}", 6, 900)
    async with write_lock:
        refund = await db.get(RefundRequest, refund_id)
        if not refund or refund.user_id != session_user["uid"]:
            raise HTTPException(404)
        if refund.status != "confirmation_pending":
            raise HTTPException(409, "该撤销申请当前不能确认")
        if not refund.confirmation_expires_at or refund.confirmation_expires_at < datetime.utcnow():
            refund.status = "expired"
            await db.commit()
            return RedirectResponse("/account?refund=code-expired#orders", 303)
        if not valid_confirmation(refund.refund_no, code.strip(), refund.confirmation_hash):
            refund.confirmation_attempts += 1
            if refund.confirmation_attempts >= 5:
                refund.status = "confirmation_locked"
            await db.commit()
            return RedirectResponse("/account?refund=invalid-code#orders", 303)
        refund.status = "pending_review"
        refund.confirmed_at = datetime.utcnow()
        refund.confirmation_hash = ""
        db.add(Audit(user_id=session_user["uid"], action="refund.confirm", detail=refund.refund_no, ip=request.client.host if request.client else ""))
        await db.commit()
    return RedirectResponse("/account?refund=confirmed#orders", 303)


@app.post("/account/refunds/{refund_id}/resend")
async def resend_refund_code(refund_id: int, request: Request, csrf: str = Form(), db=Depends(session)):
    session_user = guard(request)
    check_csrf(request, csrf)
    limit(request, f"refund-resend:{refund_id}", 3, 3600)
    async with write_lock:
        refund = await db.get(RefundRequest, refund_id)
        if not refund or refund.user_id != session_user["uid"]:
            raise HTTPException(404)
        if refund.status not in {"confirmation_pending", "confirmation_locked", "email_failed", "expired"} or refund.email_attempts >= 5:
            raise HTTPException(409, "该撤销申请不能继续发送确认码")
        order = await db.get(Order, refund.order_id)
        deadline = refund_deadline(order) if order else None
        if not deadline or deadline < datetime.utcnow():
            refund.status = "expired"
            await db.commit()
            raise HTTPException(410, "订单撤销窗口已关闭")
        account_user = await db.get(User, session_user["uid"])
        code = confirmation_code()
        refund.status = "confirmation_pending"
        refund.confirmation_hash = confirmation_hash(refund.refund_no, code)
        pending_hash = refund.confirmation_hash
        refund.confirmation_expires_at = datetime.utcnow() + REFUND_CONFIRMATION_TTL
        refund.confirmation_attempts = 0
        refund.email_attempts += 1
        refund.error = ""
        await db.commit()
    try:
        await send_refund_confirmation(db, refund, account_user, order, code)
        return RedirectResponse("/account?refund=code-sent#orders", 303)
    except MailDeliveryError as exc:
        async with write_lock:
            await db.refresh(refund)
            if refund.status == "confirmation_pending" and refund.confirmation_hash == pending_hash:
                refund.status, refund.error = "email_failed", str(exc)[:1000]
                await db.commit()
        return RedirectResponse("/account?refund=mail-failed#orders", 303)


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


@app.post("/instances/{instance_id}/vnc-session")
async def instance_vnc_session(instance_id: int, request: Request, csrf: str = Form(), db=Depends(session)):
    user = guard(request)
    check_csrf(request, csrf)
    limit(request, "instance-vnc", 10, 60)
    instance = await db.get(Instance, instance_id)
    if not instance or instance.user_id != user["uid"]:
        raise HTTPException(404)
    if not instance.clicd_id:
        raise HTTPException(409, "实例尚未完成交付")
    plan = await db.get(Plan, instance.plan_id)
    node = await node_for_instance(db, instance)
    client = node.client()
    try:
        remote = unwrap_data(await client.get(instance.clicd_id))
        if not isinstance(remote, dict):
            raise CLICDError("CLICD 实例响应格式无效")
        virtualization = normalize_virtualization(remote.get("virtualization") or remote.get("type")) or normalize_virtualization(plan.virtualization if plan else "")
        if virtualization != "kvm":
            raise HTTPException(400, "WebVNC 仅适用于 KVM 虚拟机")
        if container_status(remote) != "running":
            raise HTTPException(409, "请先启动 KVM 虚拟机再连接 VNC")
        container_name = str(remote.get("name") or remote.get("container_name") or "")
        if not container_name:
            raise CLICDError("CLICD 未返回实例名称")
        clicd_ticket = await client.vnc_ticket(container_name)
    except HTTPException:
        raise
    except CLICDError as exc:
        raise HTTPException(502, str(exc)) from exc
    token = create_vnc_session(user["uid"], instance.id, node.base_url, container_name, clicd_ticket)
    db.add(Audit(user_id=user["uid"], action="instance.vnc.session", detail=str(instance_id), ip=request.client.host if request.client else ""))
    await db.commit()
    return JSONResponse({"websocket_url": f"/instances/{instance.id}/vnc?session={token}", "instance": instance.name}, headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"})


@app.websocket("/instances/{instance_id}/vnc")
async def instance_vnc_proxy(websocket: WebSocket, instance_id: int):
    user = read_session(websocket.cookies.get("vps_session"))
    if not user or not websocket_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    pending = consume_vnc_session(websocket.query_params.get("session", ""), int(user["uid"]), instance_id)
    if not pending:
        await websocket.close(code=4401)
        return
    try:
        async with SessionLocal() as db:
            node = find_clicd_node(await clicd_nodes(db), pending.node_url)
        client = node.client()
        async with websocket_connect(
            client.vnc_websocket_url(pending.container_name),
            subprotocols=["binary", f"clicd-vnc-ticket.{pending.clicd_ticket}"],
            user_agent_header=CLICD_USER_AGENT,
            compression=None,
            proxy=None,
            max_size=None,
            open_timeout=10,
        ) as upstream:
            requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
            await websocket.accept(subprotocol="binary" if "binary" in requested_protocols else None)

            async def browser_to_clicd():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def clicd_to_browser():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {asyncio.create_task(browser_to_clicd()), asyncio.create_task(clicd_to_browser())}
            _, pending_tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending_tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        logger.warning("实例 %s WebVNC 代理断开：%s", instance_id, exc.__class__.__name__)
        try:
            await websocket.close(code=1011, reason="WebVNC 上游连接失败")
        except RuntimeError:
            pass


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
    models = {"用户": User, "套餐": Plan, "订单": Order, "实例": Instance, "卡密": CardItem, "退款申请": RefundRequest, "任务": Job}
    stats = {name: await db.scalar(select(func.count(model.id))) for name, model in models.items()}
    orders = (await db.execute(select(Order).order_by(Order.id.desc()).limit(30))).scalars().all()
    jobs = (await db.execute(select(Job).order_by(Job.id.desc()).limit(30))).scalars().all()
    return templates.TemplateResponse("admin.html", ctx(request, stats=stats, orders=orders, jobs=jobs, order_status_label=order_status_label))


@app.get("/admin/refunds", response_class=HTMLResponse)
async def admin_refunds(request: Request, db=Depends(session)):
    guard(request, True)
    refunds = (await db.execute(select(RefundRequest).order_by(RefundRequest.id.desc()).limit(200))).scalars().all()
    orders = {order.id: order for order in (await db.execute(select(Order).where(Order.id.in_({refund.order_id for refund in refunds})))).scalars().all()} if refunds else {}
    users = {user.id: user for user in (await db.execute(select(User).where(User.id.in_({refund.user_id for refund in refunds})))).scalars().all()} if refunds else {}
    instances = {instance.order_id: instance for instance in (await db.execute(select(Instance).where(Instance.order_id.in_({refund.order_id for refund in refunds})))).scalars().all()} if refunds else {}
    jobs = {job.ref_id: job for job in (await db.execute(select(Job).where(Job.kind == "refund", Job.ref_id.in_({refund.id for refund in refunds})))).scalars().all()} if refunds else {}
    pending_count = sum(refund.status == "pending_review" for refund in refunds)
    return templates.TemplateResponse("admin_refunds.html", ctx(request, refunds=refunds, orders=orders, users=users, instances=instances, jobs=jobs, pending_count=pending_count, refund_status_label=refund_status_label))


@app.post("/admin/refunds/{refund_id}/approve")
async def approve_refund(refund_id: int, request: Request, csrf: str = Form(), review_note: str = Form(""), db=Depends(session)):
    admin_user = guard(request, True)
    check_csrf(request, csrf)
    async with write_lock:
        refund = await db.get(RefundRequest, refund_id)
        if not refund:
            raise HTTPException(404)
        if refund.status != "pending_review":
            raise HTTPException(409, "仅待审核申请可以批准")
        refund.status = "approved"
        refund.reviewed_at = datetime.utcnow()
        refund.reviewed_by = admin_user["uid"]
        refund.review_note = review_note.strip()[:500]
        refund.error = ""
        job = (await db.execute(select(Job).where(Job.kind == "refund", Job.ref_id == refund.id))).scalar_one_or_none()
        if not job:
            db.add(Job(kind="refund", ref_id=refund.id))
        db.add(Audit(user_id=admin_user["uid"], action="refund.approve", detail=refund.refund_no, ip=request.client.host if request.client else ""))
        await db.commit()
    return RedirectResponse("/admin/refunds?approved=1", 303)


@app.post("/admin/refunds/{refund_id}/reject")
async def reject_refund(refund_id: int, request: Request, csrf: str = Form(), review_note: str = Form(), db=Depends(session)):
    admin_user = guard(request, True)
    check_csrf(request, csrf)
    async with write_lock:
        refund = await db.get(RefundRequest, refund_id)
        if not refund:
            raise HTTPException(404)
        if refund.status != "pending_review":
            raise HTTPException(409, "仅待审核申请可以拒绝")
        if not review_note.strip():
            raise HTTPException(400, "拒绝申请必须填写审核说明")
        refund.status = "rejected"
        refund.reviewed_at = datetime.utcnow()
        refund.reviewed_by = admin_user["uid"]
        refund.review_note = review_note.strip()[:500]
        db.add(Audit(user_id=admin_user["uid"], action="refund.reject", detail=refund.refund_no, ip=request.client.host if request.client else ""))
        await db.commit()
    return RedirectResponse("/admin/refunds?rejected=1", 303)


@app.post("/admin/refunds/{refund_id}/retry")
async def retry_refund(refund_id: int, request: Request, csrf: str = Form(), db=Depends(session)):
    admin_user = guard(request, True)
    check_csrf(request, csrf)
    async with write_lock:
        refund = await db.get(RefundRequest, refund_id)
        if not refund:
            raise HTTPException(404)
        if refund.status != "processing_failed":
            raise HTTPException(409, "仅处理失败的退款可以重试")
        job = (await db.execute(select(Job).where(Job.kind == "refund", Job.ref_id == refund.id))).scalar_one_or_none()
        if not job:
            db.add(Job(kind="refund", ref_id=refund.id))
        else:
            job.status, job.attempts, job.error, job.locked_at, job.run_after = "pending", 0, "", None, datetime.utcnow()
        refund.status, refund.error = "approved", ""
        db.add(Audit(user_id=admin_user["uid"], action="refund.retry", detail=refund.refund_no, ip=request.client.host if request.client else ""))
        await db.commit()
    return RedirectResponse("/admin/refunds?retry=1", 303)


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
    card_counts: dict[int, dict[str, int]] = {}
    for plan_id, status, count in (await db.execute(select(CardItem.plan_id, CardItem.status, func.count(CardItem.id)).group_by(CardItem.plan_id, CardItem.status))).all():
        card_counts.setdefault(plan_id, {})[status] = count
    card_orders = (await db.execute(select(Order).where(Order.product_type == "card").order_by(Order.id.desc()).limit(50))).scalars().all()
    card_items = {item.order_id: item for item in (await db.execute(select(CardItem).where(CardItem.order_id.in_({order.id for order in card_orders})))).scalars().all()} if card_orders else {}
    card_jobs = {job.ref_id: job for job in (await db.execute(select(Job).where(Job.kind == "deliver_card", Job.ref_id.in_({order.id for order in card_orders})))).scalars().all()} if card_orders else {}
    templates_list, error = [], ""
    try:
        nodes = await clicd_nodes(db)
        results = await asyncio.gather(*(node.client().templates() for node in nodes), return_exceptions=True)
        errors = []
        for node, result in zip(nodes, results):
            if isinstance(result, Exception):
                errors.append(f"{node.label}: {result}")
                continue
            if isinstance(result, dict):
                errors.extend(f"{node.label}: {warning}" for warning in result.get("errors", []) if warning)
            for image in unwrap(result) or []:
                image_id = str(image.get("id") or image.get("template_id") or image.get("slug") or "")
                image_type = normalize_virtualization(image.get("type") or image.get("virtualization"))
                if image_id and image_type:
                    templates_list.append({**image, "type": image_type, "_clicd_node": node.base_url, "_clicd_node_label": node.label, "_clicd_choice": plan_image_choice(node, image_id)})
        if not templates_list and not errors:
            errors.append("CLICD 未返回已启用且已下载的 LXC/KVM 镜像")
        error = "; ".join(errors)
    except Exception as exc:
        error = str(exc)
    return templates.TemplateResponse("admin_plans.html", ctx(request, plans=plans, templates_list=templates_list, error=error, card_counts=card_counts, card_orders=card_orders, card_items=card_items, card_jobs=card_jobs, plan_names={plan.id: plan.name for plan in plans}, order_status_label=order_status_label))


@app.post("/admin/plans")
async def save_plan(request: Request, csrf: str = Form(), plan_id: int = Form(0), product_type: str = Form("cloud"), name: str = Form(), slug: str = Form(), description: str = Form(""), price_cents: int = Form(), months: int = Form(1), stock: int = Form(-1), cpu: int = Form(1), memory_mb: int = Form(128), disk_gb: int = Form(1), traffic_gb: int = Form(0), network_down_mbps: int = Form(100), network_up_mbps: int = Form(50), virtualization: str = Form("lxc"), clicd_image: str = Form(""), card_delivery_note: str = Form(""), card_inventory: str = Form(""), assign_nat: bool = Form(False), port_mapping_count: int = Form(2), assign_ipv4: bool = Form(False), assign_ipv6: bool = Form(False), active: bool = Form(False), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    name, slug = name.strip(), slug.strip().lower()
    if product_type not in {"cloud", "card"} or not name or not re.fullmatch(r"[a-z0-9-]+", slug) or price_cents < 1:
        raise HTTPException(400, "套餐字段无效")
    duplicate = (await db.execute(select(Plan).where(Plan.slug == slug, Plan.id != plan_id))).scalar_one_or_none()
    if duplicate:
        raise HTTPException(409, "套餐唯一标识已存在")
    plan = await db.get(Plan, plan_id) if plan_id else Plan(name=name, slug=slug, price_cents=price_cents, cpu=0, memory_mb=0, disk_gb=0)
    if not plan:
        raise HTTPException(404)
    for key, value in {"name": name, "slug": slug, "description": description.strip()[:2000], "price_cents": price_cents, "product_type": product_type, "active": active}.items():
        setattr(plan, key, value)
    if product_type == "card":
        plan.months = 1
        plan.cpu = plan.memory_mb = plan.disk_gb = plan.traffic_gb = 0
        plan.network_down_mbps = plan.network_up_mbps = 0
        plan.virtualization = "card"
        plan.clicd_node = plan.clicd_image = plan.clicd_template_name = ""
        plan.clicd_validated_at = None
        plan.assign_nat = plan.assign_ipv4 = plan.assign_ipv6 = False
        plan.port_mapping_count = 0
        plan.card_delivery_note = card_delivery_note.strip()[:2000]
        db.add(plan)
        await db.flush()
        try:
            added, skipped = await import_card_items(db, plan, card_inventory)
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(400, str(exc)) from exc
        db.add(Audit(user_id=user["uid"], action="plan.card.save", detail=f"{slug} · 导入 {added} · 跳过 {skipped}"))
        await db.commit()
        return RedirectResponse(f"/admin/plans?cards_added={added}&cards_skipped={skipped}", 303)
    if virtualization not in {"lxc", "kvm"} or min(months, cpu, memory_mb, disk_gb) < 1:
        raise HTTPException(400, "云主机套餐字段无效")
    if assign_nat and not 2 <= port_mapping_count <= 64:
        raise HTTPException(400, "NAT 端口数量必须在 2 到 64 之间")
    nat_port_count = port_mapping_count if assign_nat else 0
    selected_node, image_id = parse_plan_image_choice(clicd_image)
    node = find_clicd_node(await clicd_nodes(db), selected_node)
    client = node.client()
    images = unwrap(await client.templates(virtualization)) or []
    matched = next((item for item in images if normalize_virtualization(item.get("type") or item.get("virtualization")) == virtualization and str(item.get("id") or item.get("template_id") or item.get("slug")) == image_id), None)
    if not matched:
        raise HTTPException(400, f"{node.label} 中未找到已启用且已下载的 {virtualization.upper()} 镜像：{image_id}")
    for key, value in {"card_delivery_note": "", "months": months, "stock": stock, "cpu": cpu, "memory_mb": memory_mb, "disk_gb": disk_gb, "traffic_gb": traffic_gb, "network_down_mbps": network_down_mbps, "network_up_mbps": network_up_mbps, "virtualization": virtualization, "clicd_node": node.base_url, "clicd_image": image_id, "clicd_template_name": str(matched.get("name") or matched.get("label") or image_id), "clicd_validated_at": datetime.utcnow(), "assign_nat": assign_nat, "port_mapping_count": nat_port_count, "assign_ipv4": assign_ipv4, "assign_ipv6": assign_ipv6}.items():
        setattr(plan, key, value)
    db.add(plan)
    db.add(Audit(user_id=user["uid"], action="plan.save", detail=slug))
    await db.commit()
    return RedirectResponse("/admin/plans", 303)


@app.post("/admin/plans/{plan_id}/cards")
async def add_plan_cards(plan_id: int, request: Request, csrf: str = Form(), card_inventory: str = Form(), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    async with write_lock:
        plan = await db.get(Plan, plan_id)
        if not plan or plan.product_type != "card":
            raise HTTPException(404)
        try:
            added, skipped = await import_card_items(db, plan, card_inventory)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        available_budget = plan.stock
        failed_orders = (await db.execute(select(Order).where(Order.plan_id == plan.id, Order.product_type == "card", Order.status == "delivery_failed").order_by(Order.paid_at, Order.id))).scalars().all()
        for order in failed_orders:
            assigned = (await db.execute(select(CardItem.id).where(CardItem.order_id == order.id))).scalar_one_or_none()
            if not assigned and available_budget <= 0:
                continue
            if not assigned:
                available_budget -= 1
            job = (await db.execute(select(Job).where(Job.kind == "deliver_card", Job.ref_id == order.id))).scalar_one_or_none()
            if not job:
                db.add(Job(kind="deliver_card", ref_id=order.id))
            else:
                job.status, job.attempts, job.error, job.locked_at, job.run_after = "pending", 0, "", None, datetime.utcnow()
            order.status = "paid"
        db.add(Audit(user_id=user["uid"], action="plan.card.import", detail=f"{plan.slug} · 导入 {added} · 跳过 {skipped}"))
        await db.commit()
    return RedirectResponse(f"/admin/plans?cards_added={added}&cards_skipped={skipped}", 303)


@app.post("/admin/orders/{order_id}/card-delivery/retry")
async def retry_card_delivery(order_id: int, request: Request, csrf: str = Form(), db=Depends(session)):
    user = guard(request, True)
    check_csrf(request, csrf)
    async with write_lock:
        order = await db.get(Order, order_id)
        if not order or order.product_type != "card" or order.status not in {"paid", "delivering", "delivery_failed"}:
            raise HTTPException(409, "该订单当前不能重试发卡")
        job = (await db.execute(select(Job).where(Job.kind == "deliver_card", Job.ref_id == order.id))).scalar_one_or_none()
        if not job:
            db.add(Job(kind="deliver_card", ref_id=order.id))
        else:
            job.status, job.attempts, job.error, job.locked_at, job.run_after = "pending", 0, "", None, datetime.utcnow()
        order.status = "paid"
        db.add(Audit(user_id=user["uid"], action="card.delivery.retry", detail=order.order_no))
        await db.commit()
    return RedirectResponse("/admin/plans?delivery_retry=1", 303)


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

        async def check_clicd(node: CLICDNode):
            client = node.client()
            await client.test()
            result = await client.templates()
            if result.get("errors"):
                raise CLICDError(f"{node.label}: {'；'.join(result['errors'])}")

        await asyncio.gather(*(check_clicd(node) for node in nodes))
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
