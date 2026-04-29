"""Pipeline entry point.

Single ticker:
    python pipeline.py AAPL
    python pipeline.py AAPL --verbose

Scout mode (screens S&P 500, then runs full pipeline on each candidate):
    python pipeline.py --scout
    python pipeline.py --scout --verbose
"""
import argparse
import sys

from ai_fund import db
from ai_fund.agents import fundamental, portfolio_manager, scout, technical


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_one(ticker: str, verbose: bool = False) -> None:
    """Run the full pipeline (fundamental → technical → PM) on a single ticker."""
    run_id = db.create_run(triggered_by=f"cli:{ticker}")
    short  = str(run_id)[:8]
    print(f"\n[run {short}] ── {ticker} ──────────────────────────────")

    try:
        # Step 1: Fundamental analysis
        print(f"[run {short}] Fundamental Analyst working on {ticker}...")
        fund_memo_id, fund_memo_text, fund_summary = fundamental.analyze(
            run_id, ticker, verbose=verbose
        )
        if fund_summary:
            print(
                f"[run {short}] Fundamental memo written ({len(fund_memo_text)} chars). "
                f"Conclusion: {fund_summary.get('conclusion')} "
                f"(conviction {fund_summary.get('conviction')}/5)"
            )
        else:
            print(f"[run {short}] Fundamental memo written — summary block missing.")

        # Step 2: Technical analysis
        print(f"[run {short}] Technical Analyst reading the chart for {ticker}...")
        tech_memo_id, tech_memo_text, tech_signal = technical.analyze(
            run_id, ticker, verbose=verbose
        )
        if tech_signal:
            print(
                f"[run {short}] Technical signal: {tech_signal.get('signal')} "
                f"(score {tech_signal.get('score')}/10) — "
                f"{tech_signal.get('timing_note')}"
            )
        else:
            print(f"[run {short}] Technical memo written — signal block missing.")

        # Step 3: Portfolio Manager synthesises both memos
        print(f"[run {short}] Portfolio Manager deciding...")
        _, decision, trade = portfolio_manager.decide(
            run_id=run_id,
            ticker=ticker,
            fundamental_memo=fund_memo_text,
            fundamental_memo_id=fund_memo_id,
            technical_memo=tech_memo_text,
            technical_memo_id=tech_memo_id,
            verbose=verbose,
        )

        action = decision["action"]
        if action == "HOLD":
            print(f"[run {short}] Decision: HOLD — no trade submitted.")
        else:
            tp = decision.get("target_price")
            print(f"[run {short}] Decision: {action} {decision['qty']} shares @ ~${tp}")
            if trade:
                print(
                    f"[run {short}] Alpaca order: "
                    f"order_id={trade['order_id']} ({trade['status']})"
                )

        db.complete_run(run_id, status="completed")
        print(f"[run {short}] Done.")

    except Exception as e:
        db.complete_run(run_id, status="failed")
        print(f"[run {short}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        raise


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI fund pipeline — run on one ticker, or let the Scout find ideas."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("ticker", nargs="?", type=str,
                       help="Stock ticker to analyse, e.g. AAPL")
    group.add_argument("--scout", action="store_true",
                       help="Run the Scout screener and pipe its candidates into the full pipeline")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every tool call as it happens")
    args = parser.parse_args()

    if args.ticker:
        # ── Single-ticker mode ──────────────────────────────────────────────
        run_one(args.ticker.upper().strip(), verbose=args.verbose)

    else:
        # ── Scout mode ──────────────────────────────────────────────────────
        print("[scout] Screening S&P 500 universe…")
        print("[scout] Value screen fetches ~100 yf.info calls — expect 60–90 s.")
        _, scout_text, watchlist = scout.run(verbose=args.verbose)

        if not watchlist:
            print("[scout] ERROR: Scout did not return a parseable watchlist.", file=sys.stderr)
            sys.exit(1)

        candidates = watchlist.get("candidates", [])
        note       = watchlist.get("scout_note", "")

        if not candidates:
            print("[scout] Scout found no candidates meeting the minimum thresholds.")
            print(f"[scout] Note: {note}")
            sys.exit(0)

        print(f"\n[scout] {note}")
        print(f"[scout] {len(candidates)} candidate(s) selected:\n")
        for c in candidates:
            strat = c.get("strategy", "?")
            vs    = c.get("value_score")
            ss    = c.get("swing_score")
            score_str = (
                f"value={vs} swing={ss}" if strat == "both"
                else f"value={vs}"        if strat == "value"
                else f"swing={ss}"
            )
            print(f"  {c['ticker']:6s}  [{strat:5s}]  {score_str:25s}  {c.get('one_liner','')}")

        print()
        # Run the full pipeline on each candidate in sequence
        for c in candidates:
            run_one(c["ticker"], verbose=args.verbose)


if __name__ == "__main__":
    main()
