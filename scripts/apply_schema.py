from __future__ import annotations

import os
import re
from pathlib import Path

from psycopg import connect


ADMIN_LOCK_KEYS = [
    0x6B4D5F3141444D4E,
    0x6B4D5F434C45414E,
    0x6B4D5F4D414E4147,
    0x6B4D5F5457495454,
    0x6B4D5F5954554245,
    0x6B4D5F4845494C49,
    0x6B4D5F434739315F,
    0x6B4D5F42414F3531,
    0x6B4D5F444F55594E,
    0x6B4D5F31384D485F,
    0x6B4D5F524F555F5F,
    0x6B4D5F4441444146,
    0x6B4D5F31384A5F5F,
    0x6B4D5F314D544946,
    0x6B4D5F39315041,
    0x6B4D5F3931504F52,
    0x6B4D5F393152425F,
    0x6B4D5F4156474F4F,
    0x6B4D5F3730354853,
    0x6B4D5F5858585449,
    0x6B4D5F4146464149,
    0x6B4D5F4154544143,
    0x6B4D5F4449525459,
    0x6B4D5F494E464C47,
    0x6B4D5F4D49535341,
    0x6B4D5F4241444E45,
    0x6B4D5F424452515F,
    0x6B4D5F54494B504F,
]

DB_SLOT_LOCK_BASE = 0x6B4D5F534C4F5400


def advisory_lock_key(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value


def acquire_admin_lock(conn) -> None:
    with conn.cursor() as cur:
        for lock_key in ADMIN_LOCK_KEYS:
            cur.execute("SELECT pg_advisory_lock(%s)", (advisory_lock_key(lock_key),))


def release_admin_lock(conn) -> None:
    with conn.cursor() as cur:
        for lock_key in reversed(ADMIN_LOCK_KEYS):
            cur.execute("SELECT pg_advisory_unlock(%s)", (advisory_lock_key(lock_key),))


def db_lock_max_writers() -> int:
    return max(1, int(os.environ.get("DB_LOCK_MAX_WRITERS", "4")))


def acquire_all_db_slots(conn) -> list[int]:
    slots = []
    with conn.cursor() as cur:
        for slot in range(db_lock_max_writers()):
            cur.execute("SELECT pg_advisory_lock(%s)", (advisory_lock_key(DB_SLOT_LOCK_BASE + slot),))
            slots.append(slot)
    return slots


def release_db_slots(conn, slots: list[int]) -> None:
    with conn.cursor() as cur:
        for slot in reversed(slots):
            cur.execute("SELECT pg_advisory_unlock(%s)", (advisory_lock_key(DB_SLOT_LOCK_BASE + slot),))


def split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_dollar_quote = False
    dollar_tag = ""

    for line in sql.splitlines():
        search_pos = 0
        while True:
            if in_dollar_quote:
                end = line.find(dollar_tag, search_pos)
                if end == -1:
                    break
                search_pos = end + len(dollar_tag)
                in_dollar_quote = False
                dollar_tag = ""
                continue

            match = re.search(r"\$[A-Za-z_0-9]*\$", line[search_pos:])
            if not match:
                break
            dollar_tag = match.group(0)
            in_dollar_quote = True
            search_pos += match.end()

        current.append(line)
        if not in_dollar_quote and line.rstrip().endswith(";"):
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []

    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL environment variable.")

    schema_path = Path(__file__).resolve().parent.parent / "shared" / "schema.sql"
    statements = split_sql(schema_path.read_text(encoding="utf-8"))

    with connect(database_url, prepare_threshold=None) as conn:
        slots = acquire_all_db_slots(conn)
        acquire_admin_lock(conn)
        try:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            conn.commit()
        finally:
            conn.rollback()
            release_admin_lock(conn)
            release_db_slots(conn, slots)

    print({"applied_statements": len(statements)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
