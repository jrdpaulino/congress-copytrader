"""Daily congressional-disclosure copy-trading agent.

Run once per trading day (see README for cron). Each run:
  fetch disclosures -> filter to signals -> check market is open -> risk-check
  -> place (or simulate) orders -> record everything in SQLite.
"""
import argparse
import json
import logging
import os
from collections import Counter
from datetime import datetime

from broker import Alpaca, BrokerError
from config import Config
from data_sources import CSVSource, QuiverSource
from risk import RiskGate
from signals import select
from state import State

log = logging.getLogger("agent")


def today_eastern():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # tzdata missing
        return datetime.now().date()


def run_once(cfg, source, broker, state, today=None, ignore_market_hours=False):
    today = today or today_eastern()
    summary = {"date": str(today), "dry_run": cfg.dry_run, "paper": cfg.paper,
               "signals": 0, "orders": 0, "deferred": 0, "rejected": 0}

    if os.path.exists(cfg.kill_switch_path):
        log.warning("Kill switch file %r present - halting.", cfg.kill_switch_path)
        summary["halted"] = True
        return summary

    disclosures = source.fetch()
    signals, rejected = select(disclosures, cfg, today, state.seen_ids())
    reasons = Counter(r for _, r in rejected if r != "already processed")
    log.info("%d disclosures -> %d signals. Filtered out: %s",
             len(disclosures), len(signals), dict(reasons) or "none")
    summary["signals"] = len(signals)
    if not signals:
        return summary

    if not ignore_market_hours and not broker.clock().get("is_open"):
        log.info("Market is closed; signals stay pending for the next run.")
        summary["market_closed"] = True
        return summary

    gate = RiskGate(cfg, broker, state, today)
    snap = gate.snapshot()

    for sig in signals:
        dec = gate.check(sig, snap)
        tag = f"{sig.side.upper()} {sig.ticker} (copying {', '.join(sig.politicians)})"
        if dec.verdict == "retry":
            log.info("DEFER  %s: %s", tag, dec.reason)
            summary["deferred"] += 1
            continue
        if dec.verdict == "reject":
            log.info("REJECT %s: %s", tag, dec.reason)
            summary["rejected"] += 1
            if not cfg.dry_run:
                state.mark(sig.disclosure_ids, "rejected", dec.reason)
            continue

        coid = f"cct-{today:%Y%m%d}-{sig.side}-{sig.ticker}"
        broker_id, status = "", "dry_run"
        if cfg.dry_run:
            log.info("[DRY RUN] would %s $%.2f - %s", tag, dec.notional, dec.reason or "ok")
        else:
            try:
                if dec.close_all:
                    order = broker.close_position(sig.ticker)
                else:
                    order = broker.market_order(sig.ticker, sig.side, dec.notional, coid)
                broker_id = str(order.get("id", "")) if isinstance(order, dict) else ""
                status = "submitted"
                log.info("ORDER  %s $%.2f -> %s", tag, dec.notional, broker_id)
            except BrokerError as exc:
                log.error("FAILED %s: %s", tag, exc)
                continue
            state.mark(sig.disclosure_ids, "submitted", broker_id)

        state.log_order(today, sig.ticker, sig.side, dec.notional, broker_id, status,
                        cfg.dry_run, sig.politicians)
        gate.apply(snap, sig, dec)
        summary["orders"] += 1
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", help="read disclosures from a CSV file instead of the Quiver API")
    ap.add_argument("--ignore-market-hours", action="store_true",
                    help="skip the market-open check (useful for dry runs)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("agent.log")])
    cfg = Config.from_env()
    mode = "DRY RUN" if cfg.dry_run else ("PAPER" if cfg.paper else "LIVE")
    log.info("Starting in %s mode", mode)

    source = CSVSource(args.csv) if args.csv else QuiverSource(cfg.quiver_api_key)
    broker = Alpaca(cfg.alpaca_key_id, cfg.alpaca_secret, paper=cfg.paper)
    summary = run_once(cfg, source, broker, State(cfg.db_path),
                       ignore_market_hours=args.ignore_market_hours)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
