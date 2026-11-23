# Congressional Disclosure Copy-Trader

A Python agent that pulls the latest US Congress stock-trade disclosures each
trading day, filters them into signals, and places (or simulates) matching
orders through Alpaca, behind a hard risk gate.

> Not financial advice. Disclosures are delayed, imprecise, and copy-trading
> them can lose money. Run it on paper for a while before risking real funds.

> **Status: the first version, superseded.** This was the starting point of a longer project. The data it relies on
> is the weak link: members of Congress can take up to 45 days to file a trade, so a daily report is rarely news.
> It was replaced by an agent that reads SEC Form 4 insider filings, which are due within two business days, then by a
> backtest-first research approach. See
> [edgar-insider-copytrader](https://github.com/jrdpaulino/edgar-insider-copytrader) and
> [market-signal-conductor](https://github.com/jrdpaulino/market-signal-conductor). No backtest was run on this
> version, and it needs a paid Quiver Quantitative API key to run against live data.

## Flow

```
Quiver API -> data_sources.py -> signals.py -> (market open?) -> risk.py -> broker.py (Alpaca)
                                                                   |
                                                    state.py (SQLite audit log + dedupe)
```

| File | Job |
|---|---|
| `data_sources.py` | Fetch + normalize disclosures (Quiver; CSV for testing) |
| `signals.py` | Filter: recency, filing lag, size bracket, asset type, allow/blocklists |
| `risk.py` | Per-order, daily, per-position and open-position limits; tradability checks |
| `broker.py` | Alpaca REST client (paper or live) |
| `state.py` | Dedupe processed disclosures; log every order |
| `agent.py` | The daily run |
| `test_agent.py` | 13 offline tests with a fake broker |

## Setup

1. `pip install -r requirements.txt`
2. Get a Quiver Quantitative API key (https://api.quiverquant.com/pricing/) and an Alpaca **paper** account's keys.
3. `cp .env.example .env` and fill in the keys.
4. `python -m unittest -v` (offline; should all pass).
5. `python agent.py --ignore-market-hours` runs a **dry run**: it logs what it *would* do and places nothing.

## Going further, in order

1. **Dry run** for several days. Read `agent.log`.
2. **Paper trading**: set `DRY_RUN=false` (keep `PAPER=true`). Orders go to Alpaca's simulated account.
3. **Live**: only after you trust it. Set `PAPER=false`, `DRY_RUN=false`, use live Alpaca keys, and
   `CONFIRM_LIVE_TRADING=I_UNDERSTAND_THE_RISKS`. Start with tiny `MAX_ORDER_USD`.

Create a file named `STOP` next to `agent.py` to halt all trading immediately.

## Scheduling (daily, automatic)

Run shortly after the US market opens, on weekdays. Example crontab (Eastern time):

```
CRON_TZ=America/New_York
45 9 * * 1-5  cd /path/to/project && /usr/bin/python3 agent.py >> cron.log 2>&1
```

Alternatives: a systemd timer, or a GitHub Actions workflow with a `schedule:` cron
(store keys as repository secrets; persist `trader_state.db` as a cache/artifact so
dedupe survives between runs). The agent checks the market clock itself, so
holidays are handled: signals stay pending until the next open day.

## Behavior to know about

- **Flat sizing.** Each signal buys `MAX_ORDER_USD` of the stock via a fractional dollar order, regardless of how big the politician's trade was (disclosures are only dollar *ranges*).
- **Buys only by default.** `FOLLOW_SALES=true` mirrors sales, but only for stocks you hold.
- **Conflicts are skipped.** If members both bought and sold the same ticker in one batch, it's ignored.
- **Idempotent.** Disclosure IDs are stored, and each order uses a per-day `client_order_id`, so a re-run won't double-buy.
- **Transient vs permanent.** Market closed / daily cap / low buying power = retry next run. Untradable or over position cap = rejected for good.
- **Dry runs never consume signals**, so a dry run won't block a later real run.

## Limits of the data (important)

Under the STOCK Act, members have up to 30 days to learn of a trade and 45 days to file it.
By the time a "daily report" appears, the trade is typically days to weeks old, so you are
not trading alongside the officials. Amounts are ranges, not exact. Taxes (including wash-sale
rules), slippage, and the fact that disclosed trades may be unrelated to future returns all apply.

## Other brokers

`broker.py` is a thin interface: `account`, `positions`, `asset`, `clock`,
`market_order`, `close_position`. Implement those for Schwab, Interactive Brokers,
or Tradier and pass your class to `run_once` in `agent.py`.
