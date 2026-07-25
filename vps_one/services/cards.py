import hashlib
import hmac
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import CardItem, Order, Plan
from ..security import decrypt, encrypt


cfg = settings()
MAX_IMPORT_ITEMS = 2000
MAX_CARD_LENGTH = 4000


def card_lines(raw: str) -> list[str]:
    values = [line.strip() for line in str(raw or "").replace("\r", "").split("\n") if line.strip()]
    if len(values) > MAX_IMPORT_ITEMS:
        raise ValueError(f"每次最多导入 {MAX_IMPORT_ITEMS} 条卡密")
    if any(len(value) > MAX_CARD_LENGTH for value in values):
        raise ValueError(f"单条卡密不能超过 {MAX_CARD_LENGTH} 个字符")
    return values


def card_fingerprint(value: str) -> str:
    return hmac.new(cfg.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def mask_card_secret(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip())
    tail = compact[-4:] if len(compact) > 4 else compact[-1:]
    return f"********{tail}" if tail else "********"


def reveal_card_secret(item: CardItem) -> str:
    return decrypt(item.secret_ciphertext)


async def refresh_card_stock(db: AsyncSession, plan: Plan) -> int:
    await db.flush()
    available = await db.scalar(select(func.count(CardItem.id)).where(CardItem.plan_id == plan.id, CardItem.status == "available")) or 0
    plan.stock = int(available)
    return plan.stock


async def import_card_items(db: AsyncSession, plan: Plan, raw: str) -> tuple[int, int]:
    values = card_lines(raw)
    if not values:
        await refresh_card_stock(db, plan)
        return 0, 0
    fingerprints = {card_fingerprint(value): value for value in values}
    existing = set((await db.execute(select(CardItem.secret_fingerprint).where(
        CardItem.plan_id == plan.id,
        CardItem.secret_fingerprint.in_(list(fingerprints)),
    ))).scalars().all())
    for fingerprint, value in fingerprints.items():
        if fingerprint in existing:
            continue
        db.add(CardItem(
            plan_id=plan.id,
            secret_ciphertext=encrypt(value),
            secret_fingerprint=fingerprint,
            masked_value=mask_card_secret(value),
        ))
    added = len(fingerprints) - len(existing)
    skipped = len(values) - added
    await refresh_card_stock(db, plan)
    return added, skipped


async def reserve_card_item(db: AsyncSession, order: Order, plan: Plan) -> CardItem:
    existing = (await db.execute(select(CardItem).where(CardItem.order_id == order.id))).scalar_one_or_none()
    if existing:
        return existing
    item = (await db.execute(select(CardItem).where(
        CardItem.plan_id == plan.id,
        CardItem.status == "available",
    ).order_by(CardItem.id).limit(1))).scalar_one_or_none()
    if not item:
        await refresh_card_stock(db, plan)
        raise ValueError("卡密库存不足")
    item.order_id = order.id
    item.status = "assigned"
    item.assigned_at = datetime.utcnow()
    item.error = ""
    await refresh_card_stock(db, plan)
    return item
