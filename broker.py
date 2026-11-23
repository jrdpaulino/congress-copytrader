"""Alpaca brokerage client (REST, no SDK needed).

Alpaca is used because it has an official API and a free paper-trading
environment. To support another broker (Schwab, Interactive Brokers, Tradier),
implement the same six methods: account, positions, asset, clock,
market_order, close_position.
"""
import logging

import requests

log = logging.getLogger(__name__)


class BrokerError(RuntimeError):
    pass


class Alpaca:
    def __init__(self, key_id, secret, paper=True, session=None):
        if not (key_id and secret):
            raise SystemExit("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set.")
        self.base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.session = session or requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret})

    def _req(self, method, path, allow_404=False, **kw):
        resp = self.session.request(method, self.base + path, timeout=30, **kw)
        if allow_404 and resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise BrokerError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def account(self):
        return self._req("GET", "/v2/account")

    def clock(self):
        return self._req("GET", "/v2/clock")

    def positions(self):
        """Return {symbol: market_value_usd}."""
        return {p["symbol"]: float(p["market_value"]) for p in self._req("GET", "/v2/positions")}

    def asset(self, symbol):
        return self._req("GET", f"/v2/assets/{symbol}", allow_404=True)

    def market_order(self, symbol, side, notional_usd, client_order_id):
        body = {
            "symbol": symbol,
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "notional": f"{notional_usd:.2f}",
            "client_order_id": client_order_id,   # Alpaca rejects duplicates -> idempotent
        }
        return self._req("POST", "/v2/orders", json=body)

    def close_position(self, symbol):
        return self._req("DELETE", f"/v2/positions/{symbol}")
