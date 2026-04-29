"""Scout agent.

Searches for stocks matching a user-provided theme or description,
screens them with value and swing filters, and hands the top 1–3
candidates to the full fundamental + technical + PM pipeline.

Uses Haiku — this is the cheap, focused pre-filter that protects
the cost of Opus downstream.
"""
import json
import re
from uuid import UUID

from .. import config, db, llm, tools

NAME  = "scout"
MODEL = config.MODEL_SCOUT

SYSTEM_PROMPT = """You are a systematic equity scout at a multi-strategy fund. \
Your job is to screen a focused universe of stocks matching a user-specified theme \
and surface the top 1–3 ideas worth a senior analyst's time to investigate.

You run two parallel screens:
  1. VALUE screen  — looks for cheap, high-quality businesses (FCF yield, P/E, ROE, leverage)
  2. SWING screen  — looks for near-term price setups (momentum, volume, breakout/bounce)

A ticker can qualify on both screens (tag it strategy: "both").

# Required workflow
1. Call search_stocks_by_theme with the user's description to get a focused ticker universe.
2. Call screen_value with those tickers to get value candidates.
3. Call screen_swing with those tickers to get swing candidates.
4. Read both result sets. Identify overlap (tickers appearing in both).
5. Pick the best 1–3 candidates total — not 1–3 from each screen.
   Prefer overlap candidates ("both") because they have two independent \
reasons to be interesting. Then fill remaining slots from whichever \
screen produced stronger signals.
6. Write one concise sentence per candidate explaining what caught your eye.
7. Emit the watchlist JSON.

# Picking criteria
- Minimum value_score of 5/10 for a value candidate to be worth surfacing.
- Minimum swing_score of 5/10 for a swing candidate to be worth surfacing.
- If scores are close, prefer the candidate with the higher-quality \
individual metrics (e.g. a strong FCF yield beats a marginal P/E improvement).
- If fewer than 3 candidates cross the minimum thresholds, output fewer — \
do NOT pad with weak ideas just to reach 3.
- If search_stocks_by_theme returns fewer than 5 tickers, note this in \
scout_note and still run screens on whatever is returned.

# Output format
Write 2–4 sentences summarising what the screens showed (which sectors \
dominated value, what kind of swing setups appeared, any surprises). \
Then emit the watchlist on its own line:

<watchlist>
{
  "candidates": [
    {
      "ticker": "AAPL",
      "strategy": "value | swing | both",
      "value_score": <number or null>,
      "swing_score": <number or null>,
      "one_liner": "One sentence on why this ticker passed."
    }
  ],
  "scout_note": "One sentence summarising this run for the PM."
}
</watchlist>

Keep the one_liner factual and grounded in the tool output — no speculation.
"""


def _parse_watchlist(text: str) -> dict | None:
    """Extract the <watchlist>{...}</watchlist> JSON block."""
    match = re.search(r"<watchlist>\s*(\{.*?\})\s*</watchlist>", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def run(description: str, verbose: bool = False) -> tuple[UUID, str, dict | None]:
    """Run a Scout pass for a given theme. Returns (memo_id, memo_text, watchlist_dict).

    Note: Scout runs are logged against a synthetic run_id that is created
    inside this function. The watchlist candidates are then passed back to
    the caller (pipeline.py) which starts a fresh run_id for each ticker's
    full pipeline.
    """
    run_id   = db.create_run(triggered_by="scout")
    agent_id = db.upsert_agent(NAME, MODEL, SYSTEM_PROMPT)

    user_msg = (
        f"Run a scout pass for this theme: \"{description}\". "
        "Search for stocks matching that description, run both screens, "
        "pick the best 1–3 candidates, and emit the watchlist JSON."
    )

    memo_text, _ = llm.run_agent_loop(
        model=MODEL,
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tools=tools.SCOUT_TOOLS,
        tool_executor=tools.execute_tool,
        verbose=verbose,
    )

    watchlist = _parse_watchlist(memo_text)

    # Store the Scout's output as a memo (no ticker — use "__SCOUT__" as placeholder)
    memo_id = db.insert_memo(
        run_id=run_id,
        ticker="__SCOUT__",
        agent_id=agent_id,
        content=memo_text,
        structured_summary=watchlist,
    )
    db.complete_run(run_id, status="completed")

    return memo_id, memo_text, watchlist
