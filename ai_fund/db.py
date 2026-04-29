"""Thin Postgres wrapper. Synchronous psycopg3 — easy to swap to async later."""
import hashlib
import json
from contextlib import contextmanager
from typing import Any, Optional
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import config


@contextmanager
def conn():
    """Yield a connection that commits on clean exit, rolls back on exception."""
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as c:
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def upsert_agent(name: str, model: str, system_prompt: str) -> int:
    """Return the agent_id for this (name, prompt-version), creating it if new."""
    prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
    with conn() as c:
        row = c.execute(
            """
            INSERT INTO agents (name, model, system_prompt_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT (name, system_prompt_hash) DO UPDATE
                SET model = EXCLUDED.model
            RETURNING id
            """,
            (name, model, prompt_hash),
        ).fetchone()
        return row["id"]


def create_run(triggered_by: str) -> UUID:
    with conn() as c:
        row = c.execute(
            "INSERT INTO runs (triggered_by) VALUES (%s) RETURNING id",
            (triggered_by,),
        ).fetchone()
        return row["id"]


def complete_run(run_id: UUID, status: str = "completed") -> None:
    with conn() as c:
        c.execute(
            "UPDATE runs SET completed_at = NOW(), status = %s WHERE id = %s",
            (status, run_id),
        )


def insert_memo(
    run_id: UUID,
    ticker: str,
    agent_id: int,
    content: str,
    structured_summary: Optional[dict[str, Any]] = None,
) -> UUID:
    with conn() as c:
        row = c.execute(
            """
            INSERT INTO memos (run_id, ticker, agent_id, content, structured_summary)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, ticker, agent_id, content,
             json.dumps(structured_summary) if structured_summary else None),
        ).fetchone()
        return row["id"]


def insert_decision(
    run_id: UUID,
    ticker: str,
    action: str,
    qty: Optional[float],
    target_price: Optional[float],
    rationale_memo_id: Optional[UUID],
    rationale: str,
) -> UUID:
    with conn() as c:
        row = c.execute(
            """
            INSERT INTO decisions
                (run_id, ticker, action, qty, target_price, rationale_memo_id, rationale)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, ticker, action, qty, target_price, rationale_memo_id, rationale),
        ).fetchone()
        return row["id"]


def insert_trade(decision_id: UUID, alpaca_order_id: str, status: str) -> UUID:
    with conn() as c:
        row = c.execute(
            """
            INSERT INTO trades (decision_id, alpaca_order_id, status)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (decision_id, alpaca_order_id, status),
        ).fetchone()
        return row["id"]
