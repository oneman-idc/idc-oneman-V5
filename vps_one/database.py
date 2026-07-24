import asyncio
from pathlib import Path
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .config import settings
from .models import Base

cfg = settings()
db_path = cfg.database_url.split("///")[-1]
Path(db_path).parent.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(cfg.database_url, pool_pre_ping=True, connect_args={"timeout": 15})
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
write_lock = asyncio.Lock()


@event.listens_for(engine.sync_engine, "connect")
def pragmas(conn, _):
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


MIGRATIONS = {
    "users": {"is_active": "BOOLEAN NOT NULL DEFAULT 1", "last_login_at": "DATETIME"},
    "plans": {
        "slug": "VARCHAR(100)", "features_json": "TEXT NOT NULL DEFAULT '[]'", "stock": "INTEGER NOT NULL DEFAULT -1", "sort_order": "INTEGER NOT NULL DEFAULT 0", "virtualization": "VARCHAR(16) NOT NULL DEFAULT 'lxc'", "network_down_mbps": "INTEGER NOT NULL DEFAULT 100", "network_up_mbps": "INTEGER NOT NULL DEFAULT 50", "io_read_mbps": "INTEGER NOT NULL DEFAULT 0", "io_write_mbps": "INTEGER NOT NULL DEFAULT 0", "assign_nat": "BOOLEAN NOT NULL DEFAULT 1", "port_mapping_count": "INTEGER NOT NULL DEFAULT 2", "assign_ipv4": "BOOLEAN NOT NULL DEFAULT 0", "ipv4_count": "INTEGER NOT NULL DEFAULT 0", "assign_ipv6": "BOOLEAN NOT NULL DEFAULT 1", "ipv6_count": "INTEGER NOT NULL DEFAULT 1", "clicd_node": "VARCHAR(500) NOT NULL DEFAULT ''", "clicd_template_name": "VARCHAR(200) NOT NULL DEFAULT ''", "clicd_validated_at": "DATETIME", "created_at": "DATETIME"
    },
    "orders": {"plan_snapshot": "TEXT NOT NULL DEFAULT '{}'", "fulfilled_at": "DATETIME"},
    "instances": {"clicd_node": "VARCHAR(500) NOT NULL DEFAULT ''", "ipv6": "VARCHAR(100) NOT NULL DEFAULT ''", "management_url": "TEXT NOT NULL DEFAULT ''", "ssh_password": "TEXT NOT NULL DEFAULT ''", "access_json": "TEXT NOT NULL DEFAULT '{}'", "last_synced_at": "DATETIME"},
    "payment_events": {"platform_txn_id": "VARCHAR(150) NOT NULL DEFAULT ''", "verified": "BOOLEAN NOT NULL DEFAULT 0"},
    "jobs": {"payload": "TEXT NOT NULL DEFAULT '{}'", "locked_at": "DATETIME"},
    "audit_logs": {"ip": "VARCHAR(64) NOT NULL DEFAULT ''"},
}


async def migrate_instance_identity(conn):
    """Replace the legacy global CLICD ID constraint with a per-node constraint."""
    indexes = (await conn.execute(text("PRAGMA index_list(instances)"))).all()
    legacy_unique = False
    for index in indexes:
        if not index[2]:
            continue
        index_name = str(index[1]).replace('"', '""')
        columns = (await conn.execute(text(f'PRAGMA index_info("{index_name}")'))).all()
        if [column[2] for column in columns] == ["clicd_id"]:
            legacy_unique = True
            break
    if not legacy_unique:
        return
    await conn.execute(text("""
        CREATE TABLE instances_multi_node (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
            plan_id INTEGER NOT NULL REFERENCES plans(id),
            clicd_id VARCHAR(100),
            clicd_node VARCHAR(500) NOT NULL DEFAULT '',
            name VARCHAR(100) NOT NULL,
            status VARCHAR(30) NOT NULL,
            ip VARCHAR(100) NOT NULL,
            ipv6 VARCHAR(100) NOT NULL DEFAULT '',
            ssh_port INTEGER NOT NULL,
            management_url TEXT NOT NULL DEFAULT '',
            ssh_password TEXT NOT NULL DEFAULT '',
            access_json TEXT NOT NULL DEFAULT '{}',
            expires_at DATETIME,
            last_synced_at DATETIME,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_instances_clicd_node_id UNIQUE (clicd_node, clicd_id)
        )
    """))
    await conn.execute(text("""
        INSERT INTO instances_multi_node (
            id, user_id, order_id, plan_id, clicd_id, clicd_node, name, status, ip, ipv6,
            ssh_port, management_url, ssh_password, access_json, expires_at, last_synced_at, created_at
        )
        SELECT id, user_id, order_id, plan_id, clicd_id, COALESCE(clicd_node, ''), name, status,
               ip, ipv6, ssh_port, management_url, ssh_password, access_json, expires_at,
               last_synced_at, created_at
        FROM instances
    """))
    await conn.execute(text("DROP TABLE instances"))
    await conn.execute(text("ALTER TABLE instances_multi_node RENAME TO instances"))


async def normalize_plan_nat_port_counts(conn):
    columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(plans)"))}
    if not {"assign_nat", "port_mapping_count"}.issubset(columns):
        return
    await conn.execute(text("UPDATE plans SET port_mapping_count = 0 WHERE assign_nat = 0 AND port_mapping_count != 0"))
    await conn.execute(text("UPDATE plans SET port_mapping_count = 2 WHERE assign_nat = 1 AND port_mapping_count < 2"))
    await conn.execute(text("UPDATE plans SET port_mapping_count = 64 WHERE assign_nat = 1 AND port_mapping_count > 64"))


async def migrate(conn):
    for table, columns in MIGRATIONS.items():
        rows = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in rows}
        if not existing:
            continue
        for name, definition in columns.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    await migrate_instance_identity(conn)
    await normalize_plan_nat_port_counts(conn)
    await conn.execute(text("UPDATE plans SET slug = 'plan-' || id WHERE slug IS NULL OR slug = ''"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_plans_slug ON plans(slug)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_status ON orders(user_id,status)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_dispatch ON jobs(status,run_after,id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_instances_user_status ON instances(user_id,status)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_instances_user_id ON instances(user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_instances_status ON instances(status)"))
    await conn.execute(text("PRAGMA optimize"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate(conn)


async def session():
    async with SessionLocal() as db:
        yield db
