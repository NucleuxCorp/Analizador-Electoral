import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from src.utils.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path("data/urls")
URLS_FILE = DATA_DIR / "e14_urls.jsonl"
DB_FILE   = DATA_DIR / "checkpoint.db"


async def init_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                cod_dpto       TEXT NOT NULL,
                cod_mpio       TEXT NOT NULL,
                zona           TEXT NOT NULL,
                cod_puesto     TEXT NOT NULL,
                last_page      INTEGER DEFAULT 0,
                completed      INTEGER DEFAULT 0,
                pending        INTEGER DEFAULT 0,
                urls_collected INTEGER DEFAULT 0,
                porcentaje     INTEGER DEFAULT 0,
                updated_at     TEXT,
                PRIMARY KEY (cod_dpto, cod_mpio, zona, cod_puesto)
            )
        """)
        # Schema migrations for existing databases
        existing = {row[1] async for row in await db.execute("PRAGMA table_info(progress)")}
        for col, definition in [
            ("pending",    "INTEGER DEFAULT 0"),
            ("porcentaje", "INTEGER DEFAULT 0"),
        ]:
            if col not in existing:
                await db.execute(f"ALTER TABLE progress ADD COLUMN {col} {definition}")
        await db.commit()
    logger.info(f"Storage ready — DB: {DB_FILE}, JSONL: {URLS_FILE}")


def append_url(record: dict) -> None:
    """Append one URL record to the JSONL output file."""
    with open(URLS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def is_completed(
    cod_dpto: str, cod_mpio: str, zona: str, cod_puesto: str
) -> bool:
    if not DB_FILE.exists():
        return False
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT completed FROM progress "
            "WHERE cod_dpto=? AND cod_mpio=? AND zona=? AND cod_puesto=?",
            (cod_dpto, cod_mpio, zona, cod_puesto),
        )
        row = await cur.fetchone()
        return bool(row and row[0])


async def save_checkpoint(
    cod_dpto: str,
    cod_mpio: str,
    zona: str,
    cod_puesto: str,
    last_page: int,
    urls_collected: int,
    completed: bool,
    pending: bool = False,
    porcentaje: int = 0,
) -> None:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO progress
                (cod_dpto, cod_mpio, zona, cod_puesto, last_page, completed, pending, urls_collected, porcentaje, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cod_dpto, cod_mpio, zona, cod_puesto) DO UPDATE SET
                last_page      = excluded.last_page,
                completed      = excluded.completed,
                pending        = excluded.pending,
                urls_collected = urls_collected + excluded.urls_collected,
                porcentaje     = excluded.porcentaje,
                updated_at     = excluded.updated_at
            """,
            (
                cod_dpto, cod_mpio, zona, cod_puesto,
                last_page, int(completed), int(pending),
                urls_collected, porcentaje,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def get_stats() -> dict:
    stats = {
        "total_puestos": 0,
        "completed_puestos": 0,
        "pending_puestos": 0,
        "total_urls": 0,
    }
    if DB_FILE.exists():
        async with aiosqlite.connect(DB_FILE) as db:
            cur = await db.execute(
                "SELECT COUNT(*), SUM(completed), SUM(pending) FROM progress"
            )
            row = await cur.fetchone()
            if row:
                stats["total_puestos"]     = row[0] or 0
                stats["completed_puestos"] = int(row[1] or 0)
                stats["pending_puestos"]   = int(row[2] or 0)

    if URLS_FILE.exists():
        with open(URLS_FILE, encoding="utf-8") as f:
            stats["total_urls"] = sum(1 for _ in f)

    return stats


async def get_pending_summary() -> list[dict]:
    """Return all rows marked as pending (0% — no PDFs available yet)."""
    if not DB_FILE.exists():
        return []
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT cod_dpto, cod_mpio, zona, cod_puesto, porcentaje, updated_at "
            "FROM progress WHERE pending=1 ORDER BY cod_dpto, cod_mpio"
        )
        rows = await cur.fetchall()
        return [
            {
                "cod_dpto": r[0], "cod_mpio": r[1],
                "zona": r[2], "cod_puesto": r[3],
                "porcentaje": r[4], "updated_at": r[5],
            }
            for r in rows
        ]


async def reset_all() -> None:
    if DB_FILE.exists():
        DB_FILE.unlink()
    if URLS_FILE.exists():
        URLS_FILE.unlink()
    await init_storage()
    logger.info("Checkpoint and URL file reset.")
