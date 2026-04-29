# ai-fund

A multi-agent investment pipeline that screens the market, evaluates ideas, and paper-trades decisions — so you can run it in parallel with your own portfolio and compare performance over time.

## How it works

Two modes:

**Single ticker** — point it at a stock and get a full analysis + paper trade in minutes:
```
$ python pipeline.py AAPL --verbose
[run abc123] ── AAPL ──────────────────────────────
[run abc123] Fundamental Analyst working on AAPL...
[run abc123] Fundamental memo written (2140 chars). Conclusion: LONG (conviction 4/5)
[run abc123] Technical Analyst reading the chart for AAPL...
[run abc123] Technical signal: BULLISH (score 8/10) — price holding above key support with accumulation volume
[run abc123] Portfolio Manager deciding...
[run abc123] Decision: BUY 5 shares @ ~$192.40
[run abc123] Alpaca order: order_id=xxx (accepted)
[run abc123] Done.
```

**Scout mode** — you describe a theme, the Scout finds matching stocks, runs value and swing screens, and hands the top 1–3 candidates to the full pipeline:
```
$ python pipeline.py --scout "software companies benefiting from the AI transition"
[scout] Searching for stocks matching: "software companies benefiting from the AI transition"
[scout] Running value and swing screens on theme results...

[scout] Both screens dominated by cloud infrastructure names; one payments company appeared in value.
[scout] 3 candidate(s) selected:

  MSFT    [both ]  value=7.5 swing=6.2     Strong FCF yield at 4.1% with breakout setup
  SNOW    [swing]  swing=6.8               Volume surge 1.8x average on bounce from support
  PYPL    [value]  value=7.0               Trading at 13x forward earnings with low leverage

[run abc123] ── MSFT ──────────────────────────────
...
```

---

## Tech stack

| Layer | Technology |
|---|---|
| **AI / agents** | [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) — Claude Haiku, Sonnet, and Opus models |
| **Market data** | [yfinance](https://github.com/ranaroussi/yfinance) — OHLCV, fundamentals, financials, news |
| **Brokerage** | [Alpaca](https://alpaca.markets) — paper (and live) order execution via `alpaca-py` |
| **Database** | PostgreSQL 14+ via [psycopg3](https://www.psycopg.org/psycopg3/) |
| **API server** | [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org) |
| **Validation** | [Pydantic v2](https://docs.pydantic.dev) — structured agent output parsing |
| **Runtime** | Python 3.11+ |

---

## Setup

Requires Python 3.11+, Postgres 14+, and accounts at:
- [Anthropic](https://console.anthropic.com) — for the agent LLMs
- [Alpaca](https://alpaca.markets) — paper trading is free

```bash
# 1. Create and activate a virtual environment
python -m venv venv && source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create the database
createdb ai_fund
psql ai_fund < schema.sql

# 4. Configure secrets
cp .env.example .env
# required: ANTHROPIC_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY, DATABASE_URL
# optional: ALPACA_PAPER (default: true — paper trading; set to false for live)

# 5. (Optional) Personalize your investment principles
#    Edit ai_fund/investment_principles.py — plain-English preferences the PM weighs
#    alongside its quantitative rules. Changes are picked up on the next run.

# 6. Run the pipeline
python pipeline.py AAPL
python pipeline.py AAPL --verbose
python pipeline.py --scout "software companies benefiting from the AI transition"

# 6. Start the web dashboard (optional)
uvicorn server:app --reload --port 8000
# open http://localhost:8000
```

---

## The agent team

Each agent maps to a distinct role in a real fund. They are isolated by design: each agent only has access to the tools it needs, and only the Portfolio Manager can submit orders.

### Scout
**Model:** Haiku · **File:** `ai_fund/agents/scout.py`

The top of the funnel. You give it a theme or sector description in plain English; it finds a focused stock universe matching that description, runs two independent screens, and selects the 1–3 highest-conviction ideas to hand off downstream. Uses the cheapest model because it runs at high volume — protecting the cost of Opus further down the pipeline.

**Required workflow:**
1. `search_stocks_by_theme` — translates your description into a focused ticker universe (e.g. "AI semiconductor companies" → NVDA, AMD, AVGO, …).
2. **Value screen** — scores the results on FCF yield, forward P/E, return on equity, and debt/equity ratio.
3. **Swing screen** — scores on price vs 50-day SMA, volume surge vs 20-day average, and proximity to 52-week high (breakout) or low (bounce).

Tickers appearing in both screens are tagged `strategy: "both"` and get priority — two independent signals agreeing is stronger evidence than either alone. The Scout will return fewer than 3 candidates rather than pad results with weak ideas (minimum score threshold: 5/10 on either screen).

**Outputs:** A `<watchlist>` JSON block stored in Postgres, plus a one-liner rationale per candidate.

---

### Fundamental Analyst
**Model:** Opus · **File:** `ai_fund/agents/fundamental.py`

The core research engine. Takes a single ticker and writes a long-form investment memo in the style of a value fund analyst. Every quantitative claim must come from a tool call — the agent is explicitly forbidden from citing numbers from memory.

**Required workflow** (enforced by system prompt):
1. `get_fundamentals` — valuation multiples, margins, balance-sheet ratios
2. `get_financials` — 4 years of income statement, balance sheet, cash flow
3. `get_recent_news` — recent headlines to catch material events
4. `get_quote` — current live price

**Memo structure:** Business & moat → Financial health → Valuation → Bull case → Bear case / key risks → Conclusion.

**Outputs:** Full prose memo stored in Postgres, plus a `<summary>` JSON block containing `conclusion` (LONG / NEUTRAL / AVOID), `conviction` (1–5), `fair_value`, `current_price`, and `thesis_one_liner`. The PM reads the structured summary to apply hard rules without parsing prose.

---

### Technical Analyst
**Model:** Sonnet · **File:** `ai_fund/agents/technical.py`

A timing filter. Receives the same ticker after the Fundamental Analyst and reads the chart to answer one question: *is now a good time to act on the fundamental thesis?*

Focuses on volume analysis and support/resistance — not lagging indicators like RSI or MACD. All computation happens in Python before the model sees any numbers, so the agent is purely reasoning over pre-computed results.

**Indicators computed in `get_price_history`:**
- **OBV (on-balance volume)** with 20-day trend and divergence detection. A rising OBV against a falling price is a bullish divergence — smart money accumulating into weakness. A falling OBV against a rising price is bearish divergence — institutions selling into retail strength.
- **Rolling 20-day VWAP** — where price sits relative to volume-weighted average price tells you institutional bias (above = net long, below = net short).
- **Support / resistance** derived from pivot highs/lows over 6 months, clustered within 1.5%, ranked by touch count. More touches = stronger level.
- **Volume profile** — ratio of up-day vs down-day volume over the last 20 sessions, returning a verdict of accumulation, distribution, or neutral.

**Score bands (1–10):**
- 8–10 BULLISH: Price above VWAP, accumulation volume, OBV confirming, near strong support
- 4–7 NEUTRAL: Mixed signals, price near VWAP, no clear pattern
- 1–3 BEARISH: Price below VWAP, distribution volume, OBV diverging bearishly

**Outputs:** Short prose analysis (≤200 words) plus a `<signal>` JSON block containing `signal`, `score`, nearest support/resistance levels, `volume_verdict`, `obv_signal`, `pct_vs_vwap`, and a `timing_note`.

---

### Portfolio Manager
**Model:** Opus · **File:** `ai_fund/agents/portfolio_manager.py`

The only agent with execution authority. Receives both the fundamental memo and the technical signal, checks the live portfolio state, and makes the buy/sell/hold call.

The technical score gates execution on a strong fundamental case — it cannot force a trade on a weak one.

**Hard constraints (enforced by system prompt):**

| Condition | Action |
|---|---|
| Fundamental conviction < 3 | HOLD, regardless of technical score |
| Technical score ≥ 7 + strong fundamentals | BUY at full allowed size |
| Technical score 4–6 + conviction ≥ 4 | BUY at half size |
| Technical score 4–6 + conviction = 3 | HOLD, note "waiting for better entry" |
| Technical score ≤ 3 | HOLD even on strong fundamentals |
| Memo = AVOID and holding the ticker | SELL (technical score cannot block this) |
| Already holding + memo = LONG | Only add if thesis materially strengthened |
| Max position size | 5% of portfolio equity |
| Max single trade | 25% of buying power |

**Required workflow:** `get_account` → `get_positions` → `get_quote` → decision → order.

**Outputs:** 3–5 sentence rationale citing both memos, plus a `<decision>` JSON block with `action`, `qty`, `target_price`, and `rationale_summary`. If the output is unparseable for any reason, the PM defaults to HOLD.

---

## Data flow

```
                     ┌──────────────────────────┐
                     │          Scout           │  Haiku
                     │  theme search + screens  │
                     └───────────┬──────────────┘
                                 │ watchlist (1–3 tickers)
               ┌─────────────────┼─────────────────┐
               ▼                 ▼                 ▼
        ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
        │ Fundamental │  │ Fundamental │  │ Fundamental │  Opus
        │   Analyst   │  │   Analyst   │  │   Analyst   │
        └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
               │                │                │
        ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
        │  Technical  │  │  Technical  │  │  Technical  │  Sonnet
        │   Analyst   │  │   Analyst   │  │   Analyst   │
        └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
               │                │                │
        ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
        │  Portfolio  │  │  Portfolio  │  │  Portfolio  │  Opus
        │   Manager   │  │   Manager   │  │   Manager   │
        └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
               │                │                │
               ▼                ▼                ▼
            Alpaca           Alpaca           Alpaca
         paper trade      paper trade      paper trade
```

Each ticker runs the full pipeline independently and sequentially. The three per-ticker pipelines share a Scout `run_id` in Postgres but each get their own `run_id` for their fundamental → technical → PM chain.

---

## Database schema

Everything is written to Postgres so you have a permanent audit trail.

| Table | What it stores |
|---|---|
| `agents` | One row per agent version, keyed by `(name, system_prompt_hash)`. Prompt changes create a new row automatically, so performance changes can be attributed to specific prompt versions. |
| `runs` | One row per pipeline invocation. Groups all memos and decisions for a single ticker run. |
| `memos` | Every agent's output — the prose and the structured JSON summary. Indexed by `ticker` and `run_id`. |
| `decisions` | The PM's buy/sell/hold call, linked back to the memo that justified it. |
| `trades` | Submitted Alpaca orders, including fill price and status. |
| `portfolio_snapshots` | Daily snapshots of both the AI portfolio and your real portfolio (`source: ai | user`). This is what powers the performance comparison. |

---

## Design principles

**Anti-hallucination by construction.** Every quantitative claim in any memo must come from a tool call. The system prompts forbid citing numbers from memory. Each agent's tool list is scoped to only what it needs — the Fundamental Analyst cannot call Alpaca, and the Technical Analyst cannot call `get_fundamentals`.

**Technical signals gate but never override fundamentals.** The Technical Analyst is a timing filter, not a thesis generator. A perfect chart cannot force a buy on a weak fundamental case. A bad chart can delay a buy on a strong one.

**Only the PM executes.** The Portfolio Manager is the single agent with access to `submit_market_order`. All other agents are read-only. This makes the execution path auditable and easy to disable.

**Prompt versioning for attribution.** System prompts are hashed (SHA-256, first 16 chars) and stored in the `agents` table. When you iterate on a prompt, the next run automatically creates a new agent row. You can join `agents → memos → decisions → trades` to answer: "did this prompt change improve returns?"

**Model tiers by task complexity.** Scout → Haiku (high volume, structured scoring), Technical → Sonnet (pattern reasoning over pre-computed numbers), Fundamental + PM → Opus (open-ended judgment, deep prose). Swapping models is a one-line change in `config.py`.

**Personalized investment principles.** `ai_fund/investment_principles.py` is a plain-English file that defines your investor profile: goal, risk tolerance, preferred sectors, market-cap preferences, ESG constraints, and behavioral guardrails. The Portfolio Manager receives this as part of its system context and weighs it alongside its quantitative rules. Edit the file directly — no restart required, changes are picked up on the next run.

---

## What's not here yet

- **Valuation Analyst** — independent DCF and comps, produces a fair value range. Planned next.
- **Risk Manager** — portfolio-level checks: sector concentration, correlation, drawdown exposure. Has veto power before the PM executes.
- **Scheduler** — cron or APScheduler to run the Scout automatically on a weekly cadence.
- **Performance tracker** — daily snapshot job + comparison dashboard (AI vs you vs SPY).
- **Backtesting harness** — replay the pipeline against historical data with vectorbt or backtrader.
