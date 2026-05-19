from __future__ import annotations

import os
from pathlib import Path

from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "shared" / "schema.sql"

CORE_TABLES = ["clients", "targets", "subscriptions", "crawl_state", "items"]


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    in_dollar = False
    dollar_tag = ""
    i = 0

    while i < len(sql_text):
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < len(sql_text) else ""

        if not in_single and not in_double and not in_dollar and ch == "-" and nxt == "-":
            while i < len(sql_text) and sql_text[i] != "\n":
                current.append(sql_text[i])
                i += 1
            continue

        if not in_single and not in_double and ch == "$":
            j = i + 1
            while j < len(sql_text) and (sql_text[j].isalnum() or sql_text[j] == "_"):
                j += 1
            if j < len(sql_text) and sql_text[j] == "$":
                tag = sql_text[i : j + 1]
                current.append(tag)
                if in_dollar and tag == dollar_tag:
                    in_dollar = False
                    dollar_tag = ""
                elif not in_dollar:
                    in_dollar = True
                    dollar_tag = tag
                i = j + 1
                continue

        if not in_double and not in_dollar and ch == "'":
            if in_single and nxt == "'":
                current.append("''")
                i += 2
                continue
            in_single = not in_single
            current.append(ch)
            i += 1
            continue

        if not in_single and not in_dollar and ch == '"':
            in_double = not in_double
            current.append(ch)
            i += 1
            continue

        if ch == ";" and not in_single and not in_double and not in_dollar:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_schema(target_conn) -> None:
    sql_text = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = split_sql_statements(sql_text)
    with target_conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)


def ensure_target_tables_empty(target_conn) -> None:
    with target_conn.cursor() as cur:
        for table in CORE_TABLES:
            cur.execute(f"SELECT COUNT(*) AS count FROM public.{table}")
            count = cur.fetchone()["count"]
            if count != 0:
                raise RuntimeError(f"Target table {table} is not empty: {count} rows")


def fetch_rows(source_conn, table: str) -> list[dict]:
    with source_conn.cursor() as cur:
        cur.execute(f"SELECT * FROM public.{table}")
        return cur.fetchall()


def adapt_value(value):
    if isinstance(value, (dict, list)):
        return Jsonb(value)
    return value


def copy_table(source_conn, target_conn, table: str) -> int:
    rows = fetch_rows(source_conn, table)
    if not rows:
        return 0

    columns = list(rows[0].keys())
    column_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    query = f"INSERT INTO public.{table} ({column_list}) VALUES ({placeholders})"
    values = [tuple(adapt_value(row[column]) for column in columns) for row in rows]

    with target_conn.cursor() as cur:
        cur.executemany(query, values)

    return len(rows)


def count_rows(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS count FROM public.{table}")
        return cur.fetchone()["count"]


def main() -> None:
    source_url = require_env("SOURCE_DATABASE_URL")
    target_url = require_env("TARGET_DATABASE_URL")

    with connect(source_url, row_factory=dict_row) as source_conn, connect(target_url, row_factory=dict_row) as target_conn:
        apply_schema(target_conn)
        target_conn.commit()

        ensure_target_tables_empty(target_conn)

        inserted_counts: dict[str, int] = {}
        try:
            for table in CORE_TABLES:
                inserted_counts[table] = copy_table(source_conn, target_conn, table)
            target_conn.commit()
        except Exception:
            target_conn.rollback()
            raise

        print("INSERTED_COUNTS", inserted_counts)
        for table in CORE_TABLES:
            print(f"VERIFY {table} source={count_rows(source_conn, table)} target={count_rows(target_conn, table)}")


if __name__ == "__main__":
    main()
