"""Database connection and schema helpers."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Load the repo-root .env so ANTHROPIC_API_KEY and DATABASE_URL are available
# to every entry point (ingest, classify) regardless of the working directory.
# api/src/db.ts reads the same file, so Node and Python cannot drift apart.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_DATABASE_URL = "postgresql://ironbark:ironbark@localhost:5544/ironbark"

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
AI_SCHEMA_PATH = Path(__file__).with_name("schema_ai.sql")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_conn() -> psycopg.Connection:
    """Open a new connection to the Ironbark database."""
    return psycopg.connect(database_url())


def apply_schema(conn: psycopg.Connection) -> None:
    """Drop and recreate every source-data table from schema.sql."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def apply_ai_schema(conn: psycopg.Connection) -> None:
    """Create incident_ai_findings if absent. Never drops it.

    AI findings cost a paid API call each and cannot be rebuilt from the CSVs,
    so they must survive a re-ingest. Keeping the DDL in its own file and out
    of apply_schema is what guarantees that.
    """
    sql = AI_SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
