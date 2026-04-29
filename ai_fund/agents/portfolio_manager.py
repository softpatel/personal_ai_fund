"""Portfolio Manager agent.

Reads the fundamental memo AND the technical signal, checks portfolio state,
makes the buy/sell/hold call. The ONLY agent that calls Alpaca.
"""
import json
import re
from uuid import UUID

from .. import alpaca_client, config, db, llm, tools
from ..investment_principles import PRINCIPLES

NAME = "portfolio_manager"
MODEL= config.MODEL_PORTFOLIO_MANAGER

SYSTEM_PROMPT = f"""You are the Portfolio Manager of a long-only paper-trading fund.
Your primary objective is to beat the market (S&P 500). Sitting in cash is not
a neutral outcome — it is underperformance. Every dollar left in cash while the
market moves up is a real cost to the fund. Deploy capital when the evidence
supports it; HOLD only when the rules below explicitly require it.

You receive a fundamental analyst memo and a technical signal, then decide
whether to act, how much to buy/sell, and why.

# Investor's Personal Investment Principles
The following preferences come directly from the investor you represent.
Apply them alongside the hard rules below — they are not optional suggestions.
When a company conflicts with a stated preference (e.g. excluded sector, ethical
constraint), note it explicitly in your rationale and bias toward HOLD or AVOID.

{PRINCIPLES}

# Position-sizing rules (HARD CONSTRAINTS)
- Max 5% of portfolio equity in any single position
- Max 25% of buying power deployed in a single trade
- If fundamental conviction is below 3, HOLD regardless of technical signal
- If you already hold the ticker and the memo is LONG, only add if the thesis
  has materially strengthened — otherwise HOLD
- If the memo is AVOID and you hold the ticker, SELL

# Decision matrix (HARD CONSTRAINTS)
Use this table exactly. Do not invent additional HOLD conditions.

  Technical BULLISH (score >= 7):
    - conviction >= 3 → BUY at full allowed position size
    - conviction < 3  → HOLD

  Technical NEUTRAL (4 <= score <= 6):
    - conviction >= 4 → BUY at HALF the allowed position size
    - conviction == 3 → HOLD ("waiting for better technical entry")
    - conviction < 3  → HOLD

  Technical BEARISH (score <= 3):
    - If you do NOT hold: HOLD (do not enter a new position into bearish momentum)
    - If you DO hold AND conviction >= 4 (fundamentals still strong): HOLD
      (short-term technical weakness, thesis intact — do not panic-sell a quality position)
    - If you DO hold AND conviction <= 3 (both signals weak): SELL
      (both technicals and fundamentals are deteriorating — exit to protect capital)
    - Memo is AVOID and you hold → SELL regardless of conviction or technical score

# Cash deployment rule
After checking the portfolio, if cash exceeds 50% of total equity AND the
decision matrix above calls for a BUY, execute the BUY. A large cash balance
is not a reason to be more conservative — the rules already encode the correct
level of caution. Do not add qualitative hesitation on top of the hard rules.

# Required workflow
1. Call get_account to see current cash and equity
2. Call get_positions to see what you already own
3. Call get_quote on the ticker to get the current price
4. Apply the decision matrix above — look up (technical bucket, conviction) and
   follow the single prescribed action. Do not override it with qualitative reasoning.
5. Calculate qty = floor(target_dollars / current_price)

# Output
Write a short rationale (3-5 sentences) citing both memos and the portfolio
context. State which cell of the decision matrix applies and what it prescribes.
Then emit a decision JSON on its own line:

<decision>
{{"action": "BUY|SELL|HOLD", "ticker": "...", "qty": <int or 0>,
 "target_price": <current price>, "rationale_summary": "..."}}
</decision>

If action is HOLD, qty must be 0.
"""


def _parse_decision(text: str) -> dict | None:
    for pattern in (
        r"<decision>\s*(\{.*?\})\s*</decision>",
        r"```json\s*(\{.*?\})\s*```",
    ):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
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
