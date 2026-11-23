"""Configuration, loaded from environment variables (and an optional .env file).

Safety defaults: paper trading ON, dry-run ON. Real orders against a live
account require BOTH PAPER=false and DRY_RUN=false AND an explicit
CONFIRM_LIVE_TRADING=I_UNDERSTAND_THE_RISKS.
"""
import os
from dataclasses import dataclass


def load_dotenv(path=".env"):
    """Tiny .env reader so we need no extra dependency."""
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name, default):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "y", "on")


def _list(name, default="", upper=False):
    raw = os.getenv(name, default)
    items = [x.strip() for x in raw.split(",")]
    items = [x.upper() if upper else x for x in items]
    return tuple(x for x in items if x or name == "ALLOWED_TICKER_TYPES")


@dataclass(frozen=True)
class Config:
    # credentials
    quiver_api_key: str = ""
    alpaca_key_id: str = ""
    alpaca_secret: str = ""

    # safety switches
    paper: bool = True
    dry_run: bool = True

    # sizing / risk limits
    max_order_usd: float = 250.0        # flat size of each copied trade
    max_daily_usd: float = 1000.0       # total buys per day
    max_position_pct: float = 0.05      # max share of equity in one ticker
    max_open_positions: int = 20

    # signal filters
    lookback_days: int = 7              # only act on reports filed in this window
    max_trade_age_days: int = 45        # skip trades older than this
    max_report_lag_days: int = 45       # skip filings later than this after the trade
    min_amount_low_usd: float = 15001.0  # skip the smallest disclosure bracket
    follow_sales: bool = False          # also mirror sales (only if we hold the stock)
    allowed_ticker_types: tuple = ("ST", "CS", "STOCK", "COMMON STOCK", "")
    politician_allowlist: tuple = ()    # empty = everyone
    ticker_blocklist: tuple = ()

    # files
    db_path: str = "trader_state.db"
    kill_switch_path: str = "STOP"      # create this file to halt all trading

    @classmethod
    def from_env(cls):
        load_dotenv()
        cfg = cls(
            quiver_api_key=os.getenv("QUIVER_API_KEY", ""),
            alpaca_key_id=os.getenv("ALPACA_KEY_ID", ""),
            alpaca_secret=os.getenv("ALPACA_SECRET_KEY", ""),
            paper=_bool("PAPER", True),
            dry_run=_bool("DRY_RUN", True),
            max_order_usd=float(os.getenv("MAX_ORDER_USD", 250)),
            max_daily_usd=float(os.getenv("MAX_DAILY_USD", 1000)),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", 0.05)),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", 20)),
            lookback_days=int(os.getenv("LOOKBACK_DAYS", 7)),
            max_trade_age_days=int(os.getenv("MAX_TRADE_AGE_DAYS", 45)),
            max_report_lag_days=int(os.getenv("MAX_REPORT_LAG_DAYS", 45)),
            min_amount_low_usd=float(os.getenv("MIN_AMOUNT_LOW_USD", 15001)),
            follow_sales=_bool("FOLLOW_SALES", False),
            allowed_ticker_types=_list("ALLOWED_TICKER_TYPES", "ST,CS,STOCK,COMMON STOCK,", upper=True),
            politician_allowlist=_list("POLITICIAN_ALLOWLIST"),
            ticker_blocklist=_list("TICKER_BLOCKLIST", upper=True),
            db_path=os.getenv("DB_PATH", "trader_state.db"),
            kill_switch_path=os.getenv("KILL_SWITCH_PATH", "STOP"),
        )
        cfg.validate()
        return cfg

    def validate(self):
        if not self.paper and not self.dry_run:
            if os.getenv("CONFIRM_LIVE_TRADING") != "I_UNDERSTAND_THE_RISKS":
                raise SystemExit(
                    "Refusing to place REAL orders. Set CONFIRM_LIVE_TRADING=I_UNDERSTAND_THE_RISKS "
                    "in your environment if you really mean it, or use PAPER=true."
                )
        if self.max_order_usd <= 0 or self.max_daily_usd < self.max_order_usd:
            raise SystemExit("MAX_ORDER_USD must be > 0 and <= MAX_DAILY_USD.")
        if not 0 < self.max_position_pct <= 1:
            raise SystemExit("MAX_POSITION_PCT must be between 0 and 1.")
