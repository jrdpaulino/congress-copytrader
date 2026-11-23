"""Offline tests: no network, no real broker. Run with: python -m unittest -v"""
import os
import tempfile
import unittest
from datetime import date, timedelta

from agent import run_once
from broker import BrokerError
from config import Config
from data_sources import from_record, parse_range
from state import State

TODAY = date(2026, 11, 16)


def rec(ticker="AAPL", who="Jane Doe", action="Purchase", tx=3, rep=1, rng="$15,001 - $50,000", ttype="ST"):
    return {"Representative": who, "Ticker": ticker, "Transaction": action, "Range": rng,
            "TransactionDate": str(TODAY - timedelta(days=tx)),
            "ReportDate": str(TODAY - timedelta(days=rep)),
            "House": "Representatives", "Party": "D", "TickerType": ttype}


class FakeSource:
    def __init__(self, records):
        self.records = records

    def fetch(self):
        return [d for d in map(from_record, self.records) if d]


class FakeBroker:
    def __init__(self, is_open=True, positions=None, equity=100000.0, fail=False):
        self.is_open, self._pos, self.equity, self.fail = is_open, positions or {}, equity, fail
        self.orders, self.closed = [], []

    def account(self):
        return {"equity": str(self.equity), "buying_power": str(self.equity), "trading_blocked": False}

    def clock(self):
        return {"is_open": self.is_open}

    def positions(self):
        return dict(self._pos)

    def asset(self, symbol):
        if symbol == "ZZZZ":
            return None
        return {"status": "active", "tradable": True, "fractionable": True}

    def market_order(self, symbol, side, notional, client_order_id):
        if self.fail:
            raise BrokerError("boom")
        self.orders.append((symbol, side, notional, client_order_id))
        return {"id": f"order-{len(self.orders)}"}

    def close_position(self, symbol):
        self.closed.append(symbol)
        return {"id": "close-1"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = State(":memory:")

    def cfg(self, **kw):
        kw.setdefault("kill_switch_path", os.path.join(self.tmp, "STOP"))
        kw.setdefault("dry_run", False)
        return Config(**kw)

    def run_agent(self, records, broker, **kw):
        cfg = kw.pop("cfg", None) or self.cfg()
        return run_once(cfg, FakeSource(records), broker, self.state, today=TODAY, **kw)


class ParsingTests(unittest.TestCase):
    def test_parse_range(self):
        self.assertEqual(parse_range("$1,001 - $15,000"), (1001.0, 15000.0))
        self.assertEqual(parse_range("$1,000,001 +"), (1000001.0, 1000001.0))
        self.assertEqual(parse_range(""), (0.0, 0.0))

    def test_bad_record_dropped(self):
        self.assertIsNone(from_record({"Ticker": "AAPL"}))


class AgentTests(Base):
    def test_dry_run_places_no_orders(self):
        b = FakeBroker()
        s = self.run_agent([rec()], b, cfg=self.cfg(dry_run=True))
        self.assertEqual(s["orders"], 1)
        self.assertEqual(b.orders, [])
        self.assertEqual(self.state.seen_ids(), set())   # dry runs don't consume signals

    def test_live_order_placed_once_then_deduped(self):
        b = FakeBroker()
        self.run_agent([rec()], b)
        self.run_agent([rec()], b)
        self.assertEqual(len(b.orders), 1)
        self.assertEqual(b.orders[0][:3], ("AAPL", "buy", 250.0))

    def test_filters(self):
        b = FakeBroker()
        records = [
            rec("SALE", action="Sale"),                      # sales ignored by default
            rec("OLDT", tx=60, rep=1),                       # trade too old
            rec("LATE", tx=50, rep=1),                       # also too old / late
            rec("TINY", rng="$1,001 - $15,000"),             # below min bracket
            rec("OPTN", ttype="OP"),                         # option, not a stock
            rec("STAL", rep=20),                             # report outside lookback
            rec("bad ticker!"),                              # invalid ticker
        ]
        s = self.run_agent(records, b)
        self.assertEqual(s["signals"], 0)
        self.assertEqual(b.orders, [])

    def test_conflicting_signals_skipped(self):
        b = FakeBroker(positions={"AAPL": 100.0})
        s = self.run_agent([rec(), rec(action="Sale", who="John Roe")], b,
                           cfg=self.cfg(follow_sales=True))
        self.assertEqual(s["signals"], 0)

    def test_market_closed_defers_without_consuming(self):
        b = FakeBroker(is_open=False)
        s = self.run_agent([rec()], b)
        self.assertTrue(s["market_closed"])
        self.assertEqual(self.state.seen_ids(), set())
        b.is_open = True
        self.run_agent([rec()], b)
        self.assertEqual(len(b.orders), 1)

    def test_kill_switch(self):
        b = FakeBroker()
        open(os.path.join(self.tmp, "STOP"), "w").close()
        s = self.run_agent([rec()], b)
        self.assertTrue(s["halted"])
        self.assertEqual(b.orders, [])

    def test_daily_cap_defers_extra_orders(self):
        b = FakeBroker()
        s = self.run_agent([rec("AAPL"), rec("MSFT"), rec("NVDA")], b,
                           cfg=self.cfg(max_order_usd=250, max_daily_usd=500))
        self.assertEqual(len(b.orders), 2)
        self.assertEqual(s["deferred"], 1)

    def test_position_cap_rejects(self):
        b = FakeBroker(equity=1000.0)   # $250 would be 25% of equity > 5% cap
        s = self.run_agent([rec()], b)
        self.assertEqual(s["rejected"], 1)
        self.assertEqual(b.orders, [])

    def test_untradable_asset_rejected(self):
        b = FakeBroker()
        s = self.run_agent([rec("ZZZZ")], b)
        self.assertEqual(s["rejected"], 1)

    def test_sale_only_when_held(self):
        cfg = self.cfg(follow_sales=True)
        b = FakeBroker(positions={})
        self.run_agent([rec(action="Sale")], b, cfg=cfg)
        self.assertEqual((b.orders, b.closed), ([], []))
        b2 = FakeBroker(positions={"AAPL": 100.0})
        State(":memory:")
        self.state = State(":memory:")
        self.run_agent([rec(action="Sale")], b2, cfg=cfg)
        self.assertEqual(b2.closed, ["AAPL"])   # position smaller than order size -> close it

    def test_broker_failure_keeps_signal_pending(self):
        b = FakeBroker(fail=True)
        self.run_agent([rec()], b)
        self.assertEqual(self.state.seen_ids(), set())


if __name__ == "__main__":
    unittest.main()
