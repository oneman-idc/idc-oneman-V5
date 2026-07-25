import secrets
import string
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Order, Wallet, WalletEntry


REFUND_WINDOW = timedelta(hours=24)


def generate_login_name(existing: set[str] | None = None) -> str:
    used = existing or set()
    for _ in range(100):
        candidate = "".join(secrets.choice(string.ascii_lowercase) for _ in range(6))
        if candidate not in used:
            return candidate
    raise RuntimeError("无法生成唯一登录名")


def payment_amount_cents(value: object) -> int:
    try:
        amount = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation) as exc:
        raise ValueError("金额格式无效") from exc
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise ValueError("金额最多保留两位小数")
    return int(amount * 100)


def parse_money_cents(value: str, minimum: int = 100, maximum: int = 5_000_000) -> int:
    cents = payment_amount_cents(value)
    if not minimum <= cents <= maximum:
        raise ValueError(f"金额必须在 {minimum / 100:.2f} 到 {maximum / 100:.2f} 之间")
    return cents


def refund_deadline(order: Order) -> datetime | None:
    return order.paid_at + REFUND_WINDOW if order.paid_at else None


def refund_eligible(order: Order, now: datetime | None = None) -> bool:
    deadline = refund_deadline(order)
    return bool((order.product_type or "cloud") == "cloud" and order.status == "fulfilled" and deadline and (now or datetime.utcnow()) <= deadline)


async def ensure_wallet(db: AsyncSession, user_id: int, currency: str = "CNY") -> Wallet:
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if wallet:
        return wallet
    wallet = Wallet(user_id=user_id, currency=currency, balance_cents=0)
    db.add(wallet)
    await db.flush()
    return wallet


async def post_wallet_entry(
    db: AsyncSession,
    wallet: Wallet,
    amount_cents: int,
    kind: str,
    reference_type: str,
    reference_id: str | int,
    description: str,
) -> WalletEntry:
    reference = str(reference_id)
    existing = (await db.execute(select(WalletEntry).where(
        WalletEntry.wallet_id == wallet.id,
        WalletEntry.kind == kind,
        WalletEntry.reference_type == reference_type,
        WalletEntry.reference_id == reference,
    ))).scalar_one_or_none()
    if existing:
        if existing.amount_cents != amount_cents:
            raise RuntimeError("钱包幂等流水金额不一致")
        return existing
    balance = wallet.balance_cents + amount_cents
    if balance < 0:
        raise ValueError("钱包余额不足")
    wallet.balance_cents = balance
    wallet.updated_at = datetime.utcnow()
    entry = WalletEntry(
        entry_no="WL" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(4).upper(),
        wallet_id=wallet.id,
        kind=kind,
        amount_cents=amount_cents,
        balance_after_cents=balance,
        reference_type=reference_type,
        reference_id=reference,
        description=description[:300],
    )
    db.add(entry)
    await db.flush()
    return entry
