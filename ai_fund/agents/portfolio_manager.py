"""Portfolio Manager agent.

Reads the fundamental memo AND the technical signal, checks portfolio state,
makes the buy/sell/hold call. The ONLY agent that calls Alpaca.
"""
import json
import re
from uuid import UUID

from .. import alpaca_client, config, db, llm, tools

NAME = "portfolio_manager"
MODEL= config.MODEL_PORTFOLIO_MANAGER

SYSTEM_PROMPT = """You are the Portfolio Manager of a long-only paper-trading fund.
You receive a fundamental analyst memo and a technical signal, then decide
whether to act, how much to buy/sell, and why.

# Position-sizing rules (HARD CONSTRAINTS)
- Max 5% of portfolio equity in any single position
- Max 25% of buying power deployed in a single trade
- If fundamental conviction is below 3, HOLD regardless of technical signal
- If you already hold the ticker and the memo is LONG, only add if the thesis
  has materially strengthened — otherwise HOLD
- If the memo is AVOID and you hold the ticker, SELL

# Technical signal rules (HARD CONSTRAINTS)
The technical score (1–10) is a timing filter. It NEVER overrides a weak
fundamental case — it only gates execution on a strong one.

  score >= 7 (BULLISH setup):
    - Act at full allowed position size.

  4 <= score <= 6 (NEUTRAL):
    - If fundamental conviction >= 4: BUY at HALF the allowed size.
    - If fundamental conviction == 3: HOLD and note "waiting for better entry".

  score <= 3 (BEARISH setup):
    - HOLD even on a strong fundamental case.
    - Exception: if the memo is AVOID and you hold, still SELL — do not
      let a poor technical signal stop a SELL triggered by deteriorating fundamentals.

# Required workflow
1. Call get_account to see current cash and equity
2. Call get_positions to see what you already own
3. Call get_quote on the ticker to get the current price
4. Apply the fundamental + technical rules above
5. Calculate qty = floor(target_dollars / current_price)

# Output
Write a short rationale (3-5 sentences) citing both memos and the portfolio
context. Then emit a decision JSON on its own line:

<decision>
{"action": "BUY|SELL|HOLD", "ticker": "...", "qty": <int or 0>,
 "target_price": <current price>, "rationale_summary": "..."}
</decision>

If action is HOLD, qty must be 0.
"""


def _parse_decision(text: str) -> dict | None:
    match = re.search(r"<decision>\s*(\{.*?\})\s*</decision>", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def decide(
    run_id: UUID,
    ticker: str,
    fundamental_memo: str,
    fundamental_memo_id: UUID,
    technical_memo: str,
    technical_memo_id: UUID,
    verbose: bool = False,
) -> tuple[UUID, dict, dict | None]:
    """Run the PM given both memos. Returns (decision_id, decision_dict, trade_dict)."""
    agent_id = db.upsert_agent(NAME, config.MODEL_PORTFOLIO_MANAGER, SYSTEM_PROMPT)

    user_msg = (
        f"Here are the two analyst memos for {ticker}. "
        "Check the portfolio, apply the rules, and make a decision.\n\n"
        f"--- FUNDAMENTAL MEMO ---\n{fundamental_memo}\n--- END FUNDAMENTAL MEMO ---\n\n"
        f"--- TECHNICAL SIGNAL ---\n{technical_memo}\n--- END TECHNICAL SIGNAL ---"
    )

    pm_text, _ = llm.run_agent_loop(
        model=config.MODEL_PORTFOLIO_MANAGER,
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tools=tools.PORTFOLIO_MANAGER_TOOLS,
        tool_executor=tools.execute_tool,
        verbose=verbose,
    )

    decision = _parse_decision(pm_text)
    if not decision:
        decision = {
            "action": "HOLD",
            "ticker": ticker,
            "qty": 0,
            "target_price": None,
            "rationale_summary": "PM output unparseable — defaulted to HOLD.",
        }

    # Use the fundamental memo as the primary rationale link
    decision_id = db.insert_decision(
        run_id=run_id,
        ticker=ticker,
        action=decision["action"],
        qty=decision.get("qty") or None,
        target_price=decision.get("target_price"),
        rationale_memo_id=fundamental_memo_id,
        rationale=pm_text,
    )

    trade_dict = None
    if decision["action"] in ("BUY", "SELL") and decision.get("qty", 0) > 0:
        side = "buy" if decision["action"] == "BUY" else "sell"
        trade_dict = alpaca_client.submit_market_order(
            ticker=ticker,
            qty=decision["qty"],
            side=side,
        )
        db.insert_trade(
            decision_id=decision_id,
            alpaca_order_id=trade_dict["order_id"],
            status=trade_dict["status"],
        )

    return decision_id, decision, trade_dict
