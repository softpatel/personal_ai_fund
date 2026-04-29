"""FastAPI dashboard server for ai-fund.

Run with:
    uvicorn server:app --reload --port 8000
"""
import asyncio
import json
import os
import uuid as _uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncIterator

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ai_fund import alpaca_client, db

app = FastAPI(title="AI Fund Dashboard")

# In-memory store: job_id → asyncio.Queue of output lines (None = sentinel)
_jobs: dict[str, asyncio.Queue] = {}

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _jsonable(obj: Any) -> Any:
    """Recursively convert psycopg types to JSON-safe Python types."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, _uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


# ── Static ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(_PROJECT_ROOT, "web", "index.html")
    with open(html_path) as f:
        return f.read()


# ── Account & Positions ──────────────────────────────────────────────────────

@app.get("/api/account")
async def get_account():
    return alpaca_client.get_account()


@app.get("/api/positions")
async def get_positions():
    return alpaca_client.get_positions()


# ── Run History ──────────────────────────────────────────────────────────────

@app.get("/api/runs")
async def get_runs(limit: int = 30):
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT r.id, r.triggered_by, r.started_at, r.completed_at, r.status,
                   COALESCE(
                       array_agg(DISTINCT d.ticker) FILTER (WHERE d.ticker IS NOT NULL),
                       ARRAY[]::TEXT[]
                   ) AS tickers,
                   COALESCE(
                       array_agg(DISTINCT d.action) FILTER (WHERE d.action IS NOT NULL),
                       ARRAY[]::TEXT[]
                   ) AS actions
            FROM runs r
            LEFT JOIN decisions d ON d.run_id = r.id
            GROUP BY r.id
            ORDER BY r.started_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return _jsonable([dict(r) for r in rows])


@app.get("/api/runs/{run_id}")
async def get_run_detail(run_id: str):
    with db.conn() as c:
        memos = c.execute(
            """
            SELECT m.id, m.ticker, m.content, m.structured_summary, m.created_at,
                   a.name AS agent_name, a.model
            FROM memos m
            JOIN agents a ON a.id = m.agent_id
            WHERE m.run_id = %s
            ORDER BY m.created_at
            """,
            (run_id,),
        ).fetchall()
        decisions = c.execute(
            """
            SELECT d.id, d.ticker, d.action,
                   d.qty::FLOAT, d.target_price::FLOAT,
                   d.rationale, d.created_at,
                   t.alpaca_order_id, t.status AS trade_status,
                   t.fill_price::FLOAT
            FROM decisions d
            LEFT JOIN trades t ON t.decision_id = d.id
            WHERE d.run_id = %s
            ORDER BY d.created_at
            """,
            (run_id,),
        ).fetchall()
    return _jsonable({
        "memos": [dict(m) for m in memos],
        "decisions": [dict(d) for d in decisions],
    })


# ── Pipeline Execution ───────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    ticker: str = ""
    scout: bool = False


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    ticker = req.ticker.upper().strip()
    if not req.scout and not ticker:
        raise HTTPException(400, "Provide a ticker or set scout=true")

    job_id = str(_uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = queue

    async def run_pipeline() -> None:
        cmd = ["python3", "pipeline.py", "--verbose"]
        if req.scout:
            cmd.append("--scout")
        else:
            cmd.append(ticker)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=_PROJECT_ROOT,
            )
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                await queue.put(line.decode("utf-8", errors="replace").rstrip())
            await proc.wait()
        except Exception as exc:
            await queue.put(f"[ERROR] {exc}")
        finally:
            await queue.put(None)  # sentinel — stream is done

    asyncio.create_task(run_pipeline())
    return {"job_id": job_id}


@app.get("/api/analyze/{job_id}/stream")
async def stream_output(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    queue = _jobs[job_id]

    async def generator() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield 'event: error\ndata: {"msg":"timeout"}\n\n'
                    break
                if line is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps({'line': line})}\n\n"
        finally:
            _jobs.pop(job_id, None)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
