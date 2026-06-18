import asyncpg
import decimal
import datetime
import os
from dotenv import load_dotenv

load_dotenv()


def _serialize_value(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def _row_to_dict(row) -> dict:
    return {k: _serialize_value(v) for k, v in dict(row).items()}


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "jupiterxl"),
        user=os.getenv("DB_USER", "pgmcp"),
        password=os.getenv("DB_PASSWORD", "pgmcp_dev"),
        min_size=1,
        max_size=5,
    )


async def fetch_all(pool: asyncpg.Pool, query: str, *args) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [_row_to_dict(row) for row in rows]


async def fetch_one(pool: asyncpg.Pool, query: str, *args) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return _row_to_dict(row) if row else None
