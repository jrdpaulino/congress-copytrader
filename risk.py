"""Pre-trade risk gate. Every order must pass here; the agent cannot bypass it.

Verdicts:
  ok      -> place the order
  retry   -> transient problem (daily cap, buying power); try again on the next run
  reject  -> permanent problem (not tradable, would breach position cap); never retry
"""
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Decision:
    verdict: str            # ok | retry | reject
    reason: str = ""
    notional: float = 0.0
    close_all: bool = False  # for sells that cover the whole position


class RiskGate:
    def __init__(self, cfg, broker, state, today):
        self.cfg, self.broker, self.state, self.today = cfg, broker, state, today

    def snapshot(self):
        acct = self.broker.account()
        return {
            "equity": float(acct["equity"]),
            "buying_power": float(acct["buying_power"]),
            "blocked": bool(acct.get("trading_blocked") or acct.get("account_blocked")),
            "positions": self.broker.positions(),
            "spent": self.state.spent_today(self.today, self.cfg.dry_run),
        }

    def check(self, sig, snap):
        cfg = self.cfg
        if snap["blocked"]:
            return Decision("retry", "account is blocked from trading")

        asset = self.broker.asset(sig.ticker)
        if not asset or asset.get("status") != "active" or not asset.get("tradable"):
            return Decision("reject", "asset not tradable at this broker")

        held = snap["positions"].get(sig.ticker, 0.0)

        if sig.side == "sell":
            if held <= 0:
                return Decision("reject", "no position to sell")
            if held <= sig.notional_usd:
                return Decision("ok", "closing full position", notional=held, close_all=True)
            return Decision("ok", notional=sig.notional_usd)

        # buys
        if not asset.get("fractionable"):
            return Decision("reject", "not fractionable (dollar-amount orders unsupported)")
        notional = sig.notional_usd
        if snap["equity"] <= 0:
            return Decision("retry", "no equity")
        if snap["spent"] + notional > cfg.max_daily_usd:
            return Decision("retry", f"daily cap ${cfg.max_daily_usd:,.0f} reached")
        if notional > snap["buying_power"]:
            return Decision("retry", "insufficient buying power")
        open_count = sum(1 for v in snap["positions"].values() if v > 0)
        if held <= 0 and open_count >= cfg.max_open_positions:
            return Decision("retry", "max open positions reached")
        if (held + notional) / snap["equity"] > cfg.max_position_pct:
            return Decision("reject", f"would exceed {cfg.max_position_pct:.0%} position cap")
        return Decision("ok", notional=notional)

    @staticmethod
    def apply(snap, sig, dec):
        """Update the in-memory snapshot after an order so later checks see it."""
        if sig.side == "buy":
            snap["spent"] += dec.notional
            snap["buying_power"] -= dec.notional
            snap["positions"][sig.ticker] = snap["positions"].get(sig.ticker, 0.0) + dec.notional
        elif dec.close_all:
            snap["positions"].pop(sig.ticker, None)
        else:
            snap["positions"][sig.ticker] = snap["positions"].get(sig.ticker, 0.0) - dec.notional
