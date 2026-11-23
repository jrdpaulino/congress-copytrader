"""Turn raw disclosures into a short list of trade signals, with reasons for every rejection."""
import re
from collections import defaultdict
from dataclasses import dataclass

TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


@dataclass(frozen=True)
class Signal:
    ticker: str
    side: str                # "buy" | "sell"
    disclosure_ids: tuple
    politicians: tuple
    notional_usd: float


def evaluate(d, cfg, today):
    """Return None if the disclosure is acceptable, else a short rejection reason."""
    if d.action == "buy":
        pass
    elif d.action == "sell":
        if not cfg.follow_sales:
            return "sales not followed"
    else:
        return "not a buy or sell"
    if not TICKER_RE.match(d.ticker):
        return "invalid ticker"
    if d.ticker_type.upper() not in cfg.allowed_ticker_types:
        return f"asset type {d.ticker_type!r} not allowed"
    if d.ticker in cfg.ticker_blocklist:
        return "ticker blocklisted"
    if cfg.politician_allowlist and d.politician not in cfg.politician_allowlist:
        return "politician not in allowlist"
    if d.report_date > today:
        return "report dated in the future"
    if (today - d.report_date).days > cfg.lookback_days:
        return "report older than lookback window"
    if (today - d.tx_date).days > cfg.max_trade_age_days:
        return "trade too old"
    if (d.report_date - d.tx_date).days > cfg.max_report_lag_days:
        return "late filing"
    if d.amount_low < cfg.min_amount_low_usd:
        return "below minimum size bracket"
    return None


def select(disclosures, cfg, today, seen_ids):
    """Return (signals, rejected) where rejected is a list of (disclosure, reason)."""
    rejected = []
    groups = defaultdict(list)
    for d in disclosures:
        if d.id in seen_ids:
            rejected.append((d, "already processed"))
            continue
        reason = evaluate(d, cfg, today)
        if reason:
            rejected.append((d, reason))
        else:
            groups[(d.ticker, d.action)].append(d)

    signals = []
    for (ticker, action), items in sorted(groups.items()):
        if action == "buy" and (ticker, "sell") in groups:
            rejected += [(d, "conflicting buy/sell disclosures") for d in items]
            continue
        if action == "sell" and (ticker, "buy") in groups:
            rejected += [(d, "conflicting buy/sell disclosures") for d in items]
            continue
        signals.append(Signal(
            ticker=ticker,
            side=action,
            disclosure_ids=tuple(sorted(d.id for d in items)),
            politicians=tuple(sorted({d.politician for d in items})),
            notional_usd=cfg.max_order_usd,   # flat size, regardless of how many members traded it
        ))
    return signals, rejected
