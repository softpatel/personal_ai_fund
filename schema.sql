-- ai-fund schema
-- Run: psql ai_fund < schema.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- One row per agent version. When you change a system prompt, the hash changes
-- and a new agent row gets created on the next run, so you can attribute
-- performance changes to specific prompt versions.
CREATE TABLE IF NOT EXISTS agents (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    model               TEXT NOT NULL,
    system_prompt_hash  TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (name, system_prompt_hash)
);

-- One row per pipeline invocation. Groups all memos and decisions together.
CREATE TABLE IF NOT EXISTS runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    triggered_by TEXT NOT NULL,
    started_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed'))
);

-- Each agent's written analysis for a ticker in a given run.
-- structured_summary holds the JSON the agent emits (conclusion, key metrics)
-- so downstream agents and dashboards don't have to re-parse the prose.
CREATE TABLE IF NOT EXISTS memos (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ticker              TEXT NOT NULL,
    agent_id            INTEGER NOT NULL REFERENCES agents(id),
    content             TEXT NOT NULL,
    structured_summary  JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_memos_ticker ON memos(ticker);
CREATE INDEX IF NOT EXISTS idx_memos_run    ON memos(run_id);

-- The PM's call. Links back to the memo that justified it.
CREATE TABLE IF NOT EXISTS decisions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ticker             TEXT NOT NULL,
    action             TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'HOLD')),
    qty                NUMERIC,
    target_price       NUMERIC,
    rationale_memo_id  UUID REFERENCES memos(id),
    rationale          TEXT,
    created_at         TIMESTAMPTZ DEFAULT NOW()
);

-- Submitted Alpaca orders. Updated when the broker fills.
CREATE TABLE IF NOT EXISTS trades (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id       UUID NOT NULL REFERENCES decisions(id),
    alpaca_order_id   TEXT,
    fill_price        NUMERIC,
    fill_qty          NUMERIC,
    status            TEXT NOT NULL,
    submitted_at      TIMESTAMPTZ DEFAULT NOW(),
    filled_at         TIMESTAMPTZ
);

-- Daily portfolio snapshots for both the AI and the user (you).
-- This is what powers the head-to-head comparison.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source         TEXT NOT NULL CHECK (source IN ('ai', 'user')),
    snapshot_date  DATE NOT NULL,
    holdings       JSONB NOT NULL,
    cash           NUMERIC NOT NULL,
    equity         NUMERIC NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, snapshot_date)
);
