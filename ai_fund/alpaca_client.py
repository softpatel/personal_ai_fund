"""Thin wrapper around alpaca-py for paper trading and quote lookups."""
from typing import Optional

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from . import config

_trading = TradingClient(
    api_key=config.ALPACA_API_KEY,
    secret_key=config.ALPACA_SECRET_KEY,
    paper=config.ALPACA_PAPER,
)
_data = StockHistoricalDataClient(
    api_key=config.ALPACA_API_KEY,
    secret_key=config.ALPACA_SECRET_KEY,
)


def get_latest_quote(ticker: str) -> dict:
    """Return latest bid/ask/midpoint for a ticker."""
    req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
    quote = _data.get_stock_latest_quote(req)[ticker]
    bid, ask = float(quote.bid_price), float(quote.ask_price)
    return {
        "ticker": ticker,
        "bid": bid,
        "ask": ask,
        "midpoint": round((bid + ask) / 2, 4) if bid and ask else None,
        "timestamp": quote.timestamp.isoformat(),
    }


def get_account() -> dict:
    """Cash, equity, buying power for the paper account."""
    a = _trading.get_account()
    return {
        "cash": float(a.cash),
        "equity": float(a.equity),
        "buying_power": float(a.cash),
        "portfolio_value": float(a.portfolio_value),
    }


def get_positions() -> list[dict]:
    """Current open positions in the paper portfolio."""
    return [
        {
            "ticker": p.symbol,
            "qty": float(p.qty),
            "avg_entry_price": float(p.avg_entry_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
        }
        for p in _trading.get_all_positions()
    ]


def submit_market_order(ticker: str, qty: float, side: str) -> dict:
    """Submit a market order. side ∈ {'buy', 'sell'}."""
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    req = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )
    order = _trading.submit_order(order_data=req)
    return {
        "order_id": str(order.id),
        "ticker": order.symbol,
        "qty": float(order.qty),
        "side": str(order.side).split(".")[-1].lower(),
        "status": str(order.status).split(".")[-1],
    }
