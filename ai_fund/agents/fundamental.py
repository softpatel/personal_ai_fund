"""Fundamental Analyst agent.

Takes a ticker, writes a long-form value-investing memo backed by tool-fetched data.
"""
import json
import re
from uuid import UUID

from .. import config, db, llm, tools

NAME = "fundamental_analyst"
MODEL= config.MODEL_FUNDAMENTAL

SYSTEM_PROMPT = """You are a senior equity analyst at a long-only value fund. \
Your job is to write a rigorous investment memo on a single ticker.

# Strict rules
1. Every quantitative claim (any number — ratios, growth rates, margins, prices, \
market cap) MUST come from a tool call you made in this conversation. Do NOT \
quote any number from memory. If you don't have the data, call the tool.
2. Be skeptical. Spend at least as much memo space on risks as on bull case.
3. Cite sources inline using (source: tool_name) e.g. "ROE of 147% (source: get_fundamentals)".
4. Recent news matters — pull it and weigh it in your conclusion.

# Required workflow
1. Call get_fundamentals to get valuation and profitability snapshot.
2. Call get_financials to look at multi-year trends in revenue, margins, FCF.
3. Call get_recent_news to see if anything material happened recently.
4. Call get_quote so you know the current price.
5. Then write the memo.

# Memo structure
## Business
2-3 sentences. What does this company do, what's the moat?

## Financial health
Revenue trend, margin trend, FCF trend, leverage. Numbers with citations.

## Valuation
Current multiples vs the company's history and vs sector. Is it expensive, cheap, fair?

## Bull case
Top 2-3 reasons this could compound at >12%/year for 5+ years.

## Bear case / key risks
Top 3 things that could permanently impair capital. Be specific.

## Conclusion
A single line in this exact format:
CONCLUSION: <LONG | NEUTRAL | AVOID> | conviction: <1-5> | fair_value: <number or "NA">

# After the memo
Append a JSON block on its own line, wrapped in <summary>...</summary> tags, \
matching this schema:
{"conclusion": "LONG|NEUTRAL|AVOID", "conviction": 1-5, "fair_value": <num or null>, \
"current_price": <num>, "thesis_one_liner": "..."}
"""


def _parse_summary(memo_text: str) -> dict | None:
    """Extract the <summary>{...}</summary> JSON block from the memo."""
    match = re.search(r"<summary>\s*(\{.*?\})\s*</summary>", memo_text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def analyze(run_id: UUID, ticker: str, verbose: bool = False) -> tuple[UUID, str, dict | None]:
    """Run the fundamental analyst on one ticker. Returns (memo_id, memo_text, summary)."""
    agent_id = db.upsert_agent(NAME, config.MODEL_FUNDAMENTAL, SYSTEM_PROMPT)

    user_msg = (
        f"Write a fundamental investment memo on {ticker}. "
        "Follow the workflow and structure exactly."
    )

    memo_text, _ = llm.run_agent_loop(
        model=config.MODEL_FUNDAMENTAL,
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tools=tools.FUNDAMENTAL_TOOLS,
        tool_executor=tools.execute_tool,
        verbose=verbose,
    )

    summary = _parse_summary(memo_text)
    memo_id = db.insert_memo(run_id, ticker, agent_id, memo_text, summary)
    return memo_id, memo_text, summary
