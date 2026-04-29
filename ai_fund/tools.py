"""Data-fetching tools the agents call.

DESIGN: Every quantitative claim in an agent's memo must come from one of these
functions. Agents are forbidden (in their system prompt) from quoting numbers
from memory. If a number isn't in a tool result, it doesn't go in the memo.
"""
import yfinance as yf

from . import alpaca_client


# ── Core tools ────────────────────────────────────────────────────────────────

def get_quote(ticker: str) -> dict:
    """Latest quote from Alpaca."""
    return alpaca_client.get_latest_quote(ticker)


def get_fundamentals(ticker: str) -> dict:
    """Key ratios, valuation multiples, profitability metrics from yfinance."""
    info = yf.Ticker(ticker).info
    keys = [
        "longName", "sector", "industry", "marketCap",
        "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
        "enterpriseToRevenue", "enterpriseToEbitda",
        "profitMargins", "operatingMargins", "grossMargins",
        "returnOnAssets", "returnOnEquity",
        "totalCash", "totalDebt", "debtToEquity", "currentRatio",
        "freeCashflow", "operatingCashflow",
        "earningsGrowth", "revenueGrowth",
        "dividendYield", "payoutRatio",
        "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
    ]
    return {k: info.get(k) for k in keys}


def get_financials(ticker: str) -> dict:
    """Last 4 years of income statement, balance sheet, cash flow."""
    t = yf.Ticker(ticker)
    return {
        "income_statement": _df_to_dict(t.income_stmt),
        "balance_sheet":    _df_to_dict(t.balance_sheet),
        "cash_flow":        _df_to_dict(t.cashflow),
    }


def get_recent_news(ticker: str, limit: int = 8) -> list[dict]:
    """Recent news headlines for a ticker."""
    items = yf.Ticker(ticker).news[:limit]
    out = []
    for item in items:
        c = item.get("content", item)
        out.append({
            "title":     c.get("title"),
            "publisher": c.get("provider", {}).get("displayName")
                         if isinstance(c.get("provider"), dict)
                         else c.get("publisher"),
            "url":       (c.get("canonicalUrl") or {}).get("url")
                         if isinstance(c.get("canonicalUrl"), dict)
                         else c.get("link"),
            "published": c.get("pubDate") or c.get("providerPublishTime"),
        })
    return out


def get_price_history(ticker: str, lookback_days: int = 126) -> dict:
    """OHLCV bars + pre-computed technical indicators for the Technical Analyst.

    Computes (pure pandas — no extra dependencies):
      - OBV trend and divergence signal vs price
      - Rolling 20-day VWAP and current close position vs VWAP
      - Support / resistance from pivot highs/lows, clustered ±1.5%, top 3 each
      - Volume profile: accumulation vs distribution over last 20 sessions
      - Last 20 daily closes for trend / pattern reading
    """
    t  = yf.Ticker(ticker)
    df = t.history(period=f"{lookback_days}d")
    if df.empty:
        return {"error": "no price history returned"}

    df    = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = df.index.tz_localize(None)

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]
    n     = len(df)

    # OBV
    direction    = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv          = (direction * vol).cumsum()
    obv_now      = float(obv.iloc[-1])
    obv_20d_ago  = float(obv.iloc[max(0, n - 20)])
    obv_trend    = "rising" if obv_now > obv_20d_ago else "falling"
    price_20d_ch = round(
        (float(close.iloc[-1]) - float(close.iloc[max(0, n - 20)])) /
        float(close.iloc[max(0, n - 20)]) * 100, 2
    )
    obv_pct_ch   = round((obv_now - obv_20d_ago) / max(abs(obv_20d_ago), 1) * 100, 2)

    if   obv_trend == "rising"  and price_20d_ch < -1: obv_signal = "bullish_divergence"
    elif obv_trend == "falling" and price_20d_ch >  1: obv_signal = "bearish_divergence"
    elif obv_trend == "rising":                         obv_signal = "confirming_uptrend"
    else:                                               obv_signal = "confirming_downtrend"

    # Rolling 20-day VWAP
    typical       = (high + low + close) / 3
    vwap          = ((typical * vol).rolling(20).sum() / vol.rolling(20).sum()).round(4)
    current_vwap  = round(float(vwap.iloc[-1]),  4)
    current_close = round(float(close.iloc[-1]), 4)
    pct_vs_vwap   = round((current_close - current_vwap) / current_vwap * 100, 2)

    # Support / resistance from pivot highs/lows
    WING = 2
    pivot_highs, pivot_lows = [], []
    for i in range(WING, n - WING):
        if float(high.iloc[i]) == float(high.iloc[i - WING: i + WING + 1].max()):
            pivot_highs.append(float(high.iloc[i]))
        if float(low.iloc[i])  == float(low.iloc[i  - WING: i + WING + 1].min()):
            pivot_lows.append(float(low.iloc[i]))

    def _cluster(levels: list[float], tol: float = 0.015) -> list[dict]:
        clusters: list[dict] = []
        for lvl in sorted(levels):
            merged = False
            for c in clusters:
                if abs(lvl - c["level"]) / c["level"] <= tol:
                    c["level"] = round(
                        (c["level"] * c["touches"] + lvl) / (c["touches"] + 1), 4
                    )
                    c["touches"] += 1
                    merged = True
                    break
            if not merged:
                clusters.append({"level": round(lvl, 4), "touches": 1})
        return sorted(clusters, key=lambda x: x["level"])

    all_clusters = _cluster(pivot_highs + pivot_lows)
    supports     = sorted([c for c in all_clusters if c["level"] < current_close],
                          key=lambda x: -x["level"])[:3]
    resistances  = sorted([c for c in all_clusters if c["level"] > current_close],
                          key=lambda x:  x["level"])[:3]

    # Volume profile
    recent         = df.iloc[-20:]
    avg_vol        = round(float(vol.mean()), 0)
    recent_avg_vol = round(float(recent["Volume"].mean()), 0)
    vol_ratio      = round(recent_avg_vol / max(avg_vol, 1), 2)
    up_vol         = float(recent[recent["Close"] >= recent["Open"]]["Volume"].sum())
    down_vol       = float(recent[recent["Close"] <  recent["Open"]]["Volume"].sum())
    total_vol      = up_vol + down_vol
    if   total_vol == 0:               vol_verdict = "neutral"
    elif up_vol / total_vol >= 0.60:   vol_verdict = "accumulation"
    elif down_vol / total_vol >= 0.60: vol_verdict = "distribution"
    else:                              vol_verdict = "neutral"

    open_20d      = round(float(close.iloc[max(0, n - 20)]), 4)
    recent_closes = [round(float(p), 4) for p in close.iloc[-20:].tolist()]

    return {
        "ticker": ticker, "current_close": current_close, "lookback_sessions": n,
        "obv": {"trend_20d": obv_trend, "price_change_20d_pct": price_20d_ch,
                "obv_change_20d_pct": obv_pct_ch, "signal": obv_signal},
        "vwap": {"rolling_20d": current_vwap, "current_close": current_close,
                 "pct_vs_vwap": pct_vs_vwap, "position": "above" if pct_vs_vwap > 0 else "below"},
        "support_levels":    supports,
        "resistance_levels": resistances,
        "volume_profile": {"avg_daily_vol_full_period": avg_vol,
                           "avg_daily_vol_recent_20d": recent_avg_vol,
                           "recent_vs_avg_ratio": vol_ratio, "verdict": vol_verdict},
        "price_action_20d": {"closes": recent_closes,
                             "high": round(float(high.iloc[-20:].max()), 4),
                             "low":  round(float(low.iloc[-20:].min()),  4),
                             "open": open_20d,
                             "pct_change": round((current_close - open_20d) / open_20d * 100, 2)},
    }


# ── Scout screening tools ─────────────────────────────────────────────────────

def get_screener_universe() -> dict:
    """Fetch the current S&P 500 constituent list from Wikipedia.

    Returns a dict with a 'tickers' list and a 'count'. Call this first in
    any Scout run to get the universe to pass to screen_value / screen_swing.
    """
    import io
    import pandas as pd
    import requests

    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        tables  = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        return {"error": f"Could not fetch S&P 500 list: {e}"}
    return {"tickers": tickers, "count": len(tickers)}


def search_stocks_by_theme(query: str, max_results: int = 50) -> dict:
    """Find equity tickers for a sector, industry, or theme.

    Tries yf.Industry (precise, ~10-30 stocks), then yf.Sector (broader, ~50+ stocks),
    then yf.Search as a fallback for specific company names or non-standard themes.
    Returns tickers with a 'match_type' field indicating which method succeeded.
    """
    # Try industry first (most focused)
    try:
        result = yf.Industry(query)
        df = result.top_companies
        if df is not None and len(df) > 0:
            tickers = list(df.index[:max_results])
            return {"tickers": tickers, "count": len(tickers), "query": query, "match_type": "industry"}
    except Exception:
        pass

    # Try sector (broader)
    try:
        result = yf.Sector(query)
        df = result.top_companies
        if df is not None and len(df) > 0:
            tickers = list(df.index[:max_results])
            return {"tickers": tickers, "count": len(tickers), "query": query, "match_type": "sector"}
    except Exception:
        pass

    # Fall back to free-text search
    try:
        result = yf.Search(query, max_results=max_results)
        tickers = [
            q["symbol"]
            for q in result.quotes
            if q.get("quoteType", "").upper() == "EQUITY" and q.get("symbol")
        ]
        return {"tickers": tickers, "count": len(tickers), "query": query, "match_type": "search"}
    except Exception as e:
        return {"error": f"All lookup methods failed: {e}", "query": query}


def screen_value(tickers: list[str], top_n: int = 15) -> dict:
    """Score S&P 500 tickers on value metrics and return top candidates.

    Scoring rubric (10 points total):
      FCF yield (FCF / market cap): 0–3 pts  (≥6% = 3, ≥3% = 2, ≥0% = 1)
      Forward P/E:                  0–3 pts  (≤12 = 3, ≤18 = 2, ≤25 = 1)
      Return on equity:             0–2 pts  (≥20% = 2, ≥10% = 1)
      Debt / equity:                0–2 pts  (≤50 = 2, ≤100 = 1)

    Fetches the full universe in parallel (ThreadPoolExecutor) to avoid
    the alphabetical bias from a sequential cap. Skips tickers where
    yfinance returns no usable data.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _score_ticker(ticker: str) -> dict | None:
        try:
            info   = yf.Ticker(ticker).info
            mc     = info.get("marketCap")
            fcf    = info.get("freeCashflow")
            fpe    = info.get("forwardPE")
            roe    = info.get("returnOnEquity")
            de     = info.get("debtToEquity")
            name   = info.get("longName", ticker)
            sector = info.get("sector", "Unknown")

            if not mc or mc <= 0:
                return None

            score = 0.0
            metrics: dict = {"market_cap": mc, "sector": sector}

            if fcf is not None and mc:
                fcf_yield = fcf / mc
                metrics["fcf_yield_pct"] = round(fcf_yield * 100, 2)
                if   fcf_yield >= 0.06: score += 3
                elif fcf_yield >= 0.03: score += 2
                elif fcf_yield >= 0.00: score += 1
            else:
                metrics["fcf_yield_pct"] = None

            if fpe and fpe > 0:
                metrics["forward_pe"] = round(fpe, 1)
                if   fpe <= 12: score += 3
                elif fpe <= 18: score += 2
                elif fpe <= 25: score += 1
            else:
                metrics["forward_pe"] = None

            if roe is not None:
                roe_pct = roe * 100
                metrics["roe_pct"] = round(roe_pct, 1)
                if   roe_pct >= 20: score += 2
                elif roe_pct >= 10: score += 1
            else:
                metrics["roe_pct"] = None

            if de is not None:
                metrics["debt_to_equity"] = round(de, 1)
                if   de <= 50:  score += 2
                elif de <= 100: score += 1
            else:
                metrics["debt_to_equity"] = None

            if score > 0:
                return {
                    "ticker":      ticker,
                    "name":        name,
                    "value_score": round(score, 2),
                    "strategy":    "value",
                    "metrics":     metrics,
                }
        except Exception:
            pass
        return None

    results = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_score_ticker, t): t for t in tickers}
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)

    results.sort(key=lambda x: -x["value_score"])
    return {
        "candidates": results[:top_n],
        "tickers_screened": len(tickers),
        "passing": len(results),
    }


def screen_swing(tickers: list[str], top_n: int = 15) -> dict:
    """Identify swing setups using bulk price data (one batched download).

    Scoring rubric (10 points total):
      Price vs 50-day SMA:    0–2 pts  (above SMA and SMA rising = 2, above only = 1)
      Volume surge:           0–2 pts  (recent 5d avg > 1.5× 20d avg = 2, > 1.2× = 1)
      Breakout proximity:     0–3 pts  (within 2% of 52-wk high = 3, 5% = 2, 10% = 1)
      Bounce setup:           0–3 pts  (>20% off 52-wk low but recovering = up to 3)

    Breakout and bounce are mutually exclusive: a ticker can't score both.
    Uses yf.download() for the full universe in a single request — fast.
    """
    import pandas as pd

    # Batch download — much faster than looping .history() per ticker
    subset = list(tickers)
    try:
        raw = yf.download(
            subset,
            period="1y",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as e:
        return {"error": f"yf.download failed: {e}"}

    results = []
    for ticker in subset:
        try:
            # Handle single-ticker vs multi-ticker DataFrame shape
            if len(subset) == 1:
                df = raw
            else:
                if ticker not in raw.columns.get_level_values(0):
                    continue
                df = raw[ticker]

            df = df.dropna(subset=["Close", "Volume"])
            if len(df) < 60:
                continue

            close  = df["Close"]
            volume = df["Volume"]
            n      = len(df)

            sma20  = close.rolling(20).mean()
            sma50  = close.rolling(50).mean()
            sma50_prev = sma50.shift(5)

            current   = float(close.iloc[-1])
            s20_now   = float(sma20.iloc[-1])
            s50_now   = float(sma50.iloc[-1])
            s50_prev  = float(sma50_prev.iloc[-1])
            high_52w  = float(close.rolling(252).max().iloc[-1])
            low_52w   = float(close.rolling(252).min().iloc[-1])

            avg_vol_20d  = float(volume.rolling(20).mean().iloc[-1])
            avg_vol_5d   = float(volume.iloc[-5:].mean())
            vol_ratio    = round(avg_vol_5d / max(avg_vol_20d, 1), 2)

            score = 0.0
            metrics: dict = {
                "current_price": round(current, 2),
                "sma_20":        round(s20_now, 2),
                "sma_50":        round(s50_now, 2),
                "high_52w":      round(high_52w, 2),
                "low_52w":       round(low_52w, 2),
                "volume_ratio_5d_vs_20d": vol_ratio,
            }

            # Price vs 50-day SMA
            sma_rising = s50_now > s50_prev
            if   current > s50_now and sma_rising: score += 2
            elif current > s50_now:                score += 1

            # Volume surge
            if   vol_ratio >= 1.5: score += 2
            elif vol_ratio >= 1.2: score += 1

            # Breakout vs bounce (mutually exclusive)
            pct_from_high = (high_52w - current) / high_52w * 100
            pct_from_low  = (current - low_52w)  / low_52w  * 100
            metrics["pct_from_52w_high"] = round(pct_from_high, 1)
            metrics["pct_from_52w_low"]  = round(pct_from_low,  1)

            if pct_from_high <= 10:
                # Breakout candidate
                if   pct_from_high <= 2:  score += 3
                elif pct_from_high <= 5:  score += 2
                else:                     score += 1
                metrics["setup"] = "breakout"
            elif pct_from_low >= 20 and current > s20_now:
                # Bounce candidate: pulled back significantly, now recovering
                if   pct_from_low >= 50:  score += 3
                elif pct_from_low >= 30:  score += 2
                else:                     score += 1
                metrics["setup"] = "bounce"
            else:
                metrics["setup"] = "none"

            if score >= 3:   # minimum threshold to appear in results
                results.append({
                    "ticker":      ticker,
                    "swing_score": round(score, 2),
                    "strategy":    "swing",
                    "metrics":     metrics,
                })
        except Exception:
            continue

    results.sort(key=lambda x: -x["swing_score"])
    return {
        "candidates": results[:top_n],
        "tickers_screened": len(subset),
        "passing": len(results),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _df_to_dict(df) -> dict:
    """yfinance DataFrames are Timestamp-keyed; flatten to JSON-safe dict."""
    if df is None or df.empty:
        return {}
    out = {}
    for col in df.columns:
        period = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
        out[period] = {
            str(idx): (None if _is_nan(v) else float(v))
            for idx, v in df[col].items()
        }
    return out


def _is_nan(v) -> bool:
    try:
        return v != v
    except Exception:
        return False


# ── Tool schemas ──────────────────────────────────────────────────────────────

SCOUT_TOOLS = [
    {
        "name": "search_stocks_by_theme",
        "description": (
            "Return equity tickers for a sector, industry, or theme. "
            "Tries yf.Industry (precise), then yf.Sector (broader), then free-text search as fallback. "
            "Translate the user's theme into the best matching slug before calling. "
            "Valid sector slugs: technology, financial-services, healthcare, consumer-cyclical, "
            "communication-services, industrials, consumer-defensive, energy, basic-materials, "
            "real-estate, utilities. "
            "Example industry slugs: semiconductors, semiconductor-equipment-materials, "
            "software-infrastructure, software-application, biotechnology, drug-manufacturers-general, "
            "banks-diversified, banks-regional, oil-gas-integrated, solar, uranium, "
            "specialty-chemicals, aerospace-defense, insurance-diversified. "
            "Call this first to build the universe, then pass tickers to screen_value and screen_swing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Sector or industry slug, e.g. 'semiconductors' or 'technology'",
                },
                "max_results": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max tickers to return",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "screen_value",
        "description": (
            "Score a list of tickers on value metrics (FCF yield, forward P/E, ROE, "
            "debt/equity) and return the top candidates ranked by composite score. "
            "Pass the tickers list from search_stocks_by_theme."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {"type": "array", "items": {"type": "string"},
                            "description": "List of ticker symbols to screen"},
                "top_n":   {"type": "integer", "default": 15,
                            "description": "Max candidates to return"},
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "screen_swing",
        "description": (
            "Identify swing trade setups using bulk price data. Scores on: "
            "price vs 50-day SMA, volume surge, and proximity to 52-week "
            "high (breakout) or bounce from 52-week low. Fast — single batched download."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {"type": "array", "items": {"type": "string"},
                            "description": "List of ticker symbols to screen"},
                "top_n":   {"type": "integer", "default": 15,
                            "description": "Max candidates to return"},
            },
            "required": ["tickers"],
        },
    },
]

FUNDAMENTAL_TOOLS = [
    {
        "name": "get_quote",
        "description": "Get the latest bid/ask/midpoint price for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Stock ticker, e.g. AAPL"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_fundamentals",
        "description": "Get valuation multiples, profitability margins, balance-sheet ratios, and growth metrics.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_financials",
        "description": "Get the last 4 years of income statement, balance sheet, and cash flow statement.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_recent_news",
        "description": "Get recent news headlines for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit":  {"type": "integer", "default": 8},
            },
            "required": ["ticker"],
        },
    },
]

TECHNICAL_TOOLS = [
    {
        "name": "get_quote",
        "description": "Get the latest bid/ask/midpoint price for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_price_history",
        "description": (
            "Fetch OHLCV history plus pre-computed indicators: "
            "OBV with divergence signal, rolling 20-day VWAP and position, "
            "support/resistance from pivot highs/lows (with touch counts), "
            "volume profile (accumulation vs distribution verdict), "
            "and the last 20 daily closes for pattern/trend reading."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "lookback_days": {
                    "type": "integer",
                    "default": 126,
                    "description": "Calendar days of history (default 126 ≈ 6 months)",
                },
            },
            "required": ["ticker"],
        },
    },
]

PORTFOLIO_MANAGER_TOOLS = [
    {
        "name": "get_quote",
        "description": "Get the latest bid/ask/midpoint price for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_account",
        "description": "Get current cash, equity, and buying power in the paper portfolio.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_positions",
        "description": "Get all current open positions in the paper portfolio.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ── Dispatch table ────────────────────────────────────────────────────────────

TOOL_IMPLS = {
    "get_quote":              lambda **kw: get_quote(**kw),
    "get_fundamentals":       lambda **kw: get_fundamentals(**kw),
    "get_financials":         lambda **kw: get_financials(**kw),
    "get_recent_news":        lambda **kw: get_recent_news(**kw),
    "get_price_history":      lambda **kw: get_price_history(**kw),
    "search_stocks_by_theme": lambda **kw: search_stocks_by_theme(**kw),
    "screen_value":           lambda **kw: screen_value(**kw),
    "screen_swing":           lambda **kw: screen_swing(**kw),
    "get_account":            lambda **kw: alpaca_client.get_account(),
    "get_positions":          lambda **kw: alpaca_client.get_positions(),
}


def execute_tool(name: str, args: dict):
    """Run a tool by name. Returns the result or an error dict."""
    impl = TOOL_IMPLS.get(name)
    if not impl:
        return {"error": f"unknown tool: {name}"}
    try:
        return impl(**args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
