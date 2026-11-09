import json
import os
from pathlib import Path
import asyncpg
from .config import migrate_legacy_settings


async def init_connection(conn):
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect():
    pool = await asyncpg.create_pool(host=os.getenv("DB_HOST", "db"),
        database=os.getenv("DB_NAME", "exaone"), user=os.getenv("DB_USER", "exaone"),
        password=os.getenv("DB_PASSWORD", "exaone-local-password"),
        min_size=2, max_size=12, init=init_connection)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(597631204)")
            await conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            versions = {r["version"] for r in await conn.fetch("SELECT version FROM schema_version")}
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                version = int(path.name.split("_")[0])
                if version not in versions:
                    await conn.execute(path.read_text(encoding="utf-8"))
                    if version == 2:
                        for client in await conn.fetch("SELECT id, settings FROM clients"):
                            updated = migrate_legacy_settings(client["settings"])
                            if updated != client["settings"]:
                                await conn.execute("UPDATE clients SET settings=$1 WHERE id=$2", updated, client["id"])
                    await conn.execute("INSERT INTO schema_version(version) VALUES($1)", version)
    return pool
