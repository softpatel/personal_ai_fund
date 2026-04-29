"""Technical Analyst agent.

Reads OHLCV history and pre-computed indicators, produces a structured
signal score (1–10) the Portfolio Manager weighs alongside the fundamental memo.
"""
import json
import re
from uuid import UUID

from .. import config, db, llm, tools

NAME = "technical_analyst"

# Use a faster/cheaper model here — the reasoning is mostly pattern-reading
# over pre-computed numbers, not deep prose.
MODEL = config.MODEL_TECHNICAL

SYSTEM_PROMPT = """You are a technical analyst at a quantitative hedge fund. \
You specialize in volume analysis and support/resistance — you do NOT rely on \
lagging indicators like RSI or MACD. Your job is to produce a precise, \
number-grounded technical signal that the Portfolio Manager can act on.

# Strict rules
1. Every number you cite MUST come from a get_price_history tool result. \
Do not estimate or interpolate.
2. You are a timing filter, NOT the thesis. The PM already has the fundamental \
case. Your job is to say whether the chart supports acting NOW.
3. Be concise. No more than 200 words of prose. The signal JSON is what matters.

# Required workflow
1. Call get_price_history (default lookback is fine for most cases).
2. Call get_quote for the live midpoint.
3. Write your analysis, then emit the signal JSON.

# How to score (1–10)
Score 8–10 (act now — strong technical setup):
  - Price above 20-day VWAP AND volume verdict is accumulation
  - OBV signal is confirming_uptrend or bullish_divergence
  - Current price is near (within 2%) of a high-touch support level, not resistance
  - Recent 20-day price action shows a series of higher lows

Score 5–7 (neutral — proceed but size conservatively):
  - Mixed signals: some bullish, some bearish
  - Price near VWAP (within 1%)
  - Volume neutral
  - No clear pattern in recent closes

Score 1–4 (poor timing — wait for better entry):
  - Price below VWAP AND volume verdict is distribution
  - OBV signal is confirming_downtrend or bearish_divergence
  - Price just broke through a high-touch support level (not holding)
  - Recent price action shows lower highs / lower lows

# Key concepts to apply
OBV divergence is the most important signal:
  - bullish_divergence (OBV rising, price falling): smart money accumulating \
into weakness — bullish. Weight this heavily toward a higher score.
  - bearish_divergence (OBV falling, price rising): retail chasing while \
institutions sell — bearish. Weight toward lower score.

Support/resistance with more touches = stronger level:
  - Price sitting just above a 4-touch support is a high-confidence entry zone.
  - Price approaching a 5-touch resistance with distribution volume = warning.

VWAP position tells you institutional bias:
  - Consistently above VWAP = institutions net long. Below = net short.
  - Price far above VWAP (>3%) = extended; poor risk/reward for a new long entry.

# Output format
Write 3–5 sentences of analysis citing specific numbers (with "source: get_price_history"). \
Then emit the signal on its own line, wrapped in <signal>...</signal> tags:

<signal>
{
  "signal": "BULLISH | NEUTRAL | BEARISH",
  "score": <1-10>,
  "nearest_support": <price or null>,
  "nearest_resistance": <price or null>,
  "volume_verdict": "accumulation | distribution | neutral",
  "obv_signal": "<value from tool>",
  "pct_vs_vwap": <number>,
  "timing_note": "One sentence: act now / wait for X / avoid until Y"
}
</signal>
"""


def _parse_signal(text: str) -> dict | None:
    """Extract the <signal>{...}</signal> JSON block."""
    match = re.search(r"<signal>\s*(\{.*?\})\s*</signal>", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def analyze(
    run_id: UUID,
    ticker: str,
    verbose: bool = False,
) -> tuple[UUID, str, dict | None]:
    """Run the technical analyst on one ticker.

    Returns (memo_id, memo_text, signal_dict).
    The signal_dict is also stored as structured_summary in the memos table
    so the PM can read it without parsing prose.
    """
    agent_id = db.upsert_agent(NAME, MODEL, SYSTEM_PROMPT)

    user_msg = (
        f"Produce a technical signal for {ticker}. "
        "Follow the workflow exactly: fetch price history, fetch the live quote, "
        "then write your analysis and emit the signal JSON."
    )

    memo_text, _ = llm.run_agent_loop(
        model=MODEL,
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tools=tools.TECHNICAL_TOOLS,
        tool_executor=tools.execute_tool,
        verbose=verbose,
    )

    signal = _parse_signal(memo_text)
    memo_id = db.insert_memo(run_id, ticker, agent_id, memo_text, signal)
    return memo_id, memo_text, signal
