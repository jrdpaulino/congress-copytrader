"""Fetch and normalize congressional trade disclosures.

Primary source: Quiver Quantitative's live congress trading endpoint
(House + Senate, newest first, up to 500 each). A CSV source is included for
offline testing. To add another provider, implement `fetch() -> list[Disclosure]`.
"""
import csv
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import date

import requests

log = logging.getLogger(__name__)

QUIVER_URL = "https://api.quiverquant.com/beta/live/congresstrading"


@dataclass(frozen=True)
class Disclosure:
    id: str
    politician: str
    chamber: str
    party: str
    ticker: str
    ticker_type: str
    action: str          # "buy" | "sell" | "other"
    tx_date: date
    report_date: date
    amount_low: float
    amount_high: float
    description: str = ""


_money = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_range(text):
    """'$1,001 - $15,000' -> (1001.0, 15000.0). '$1,000,001 +' -> (1000001.0, 1000001.0)."""
    nums = [float(m.replace(",", "")) for m in _money.findall(str(text or ""))]
    if not nums:
        return 0.0, 0.0
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums[:2]), max(nums[:2])


def _date(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _action(text):
    t = (text or "").strip().lower()
    if t.startswith("purchase") or t == "buy":
        return "buy"
    if "sale" in t or t == "sell":
        return "sell"
    return "other"


def from_record(rec):
    """Convert one raw provider record into a Disclosure (or None if unusable)."""
    politician = rec.get("Representative") or rec.get("Senator") or rec.get("Name") or ""
    ticker = str(rec.get("Ticker") or "").strip().upper()
    tx_date = _date(rec.get("TransactionDate"))
    report_date = _date(rec.get("ReportDate"))
    if not (politician and ticker and tx_date and report_date):
        return None
    low, high = parse_range(rec.get("Range") or rec.get("Amount"))
    action = _action(rec.get("Transaction"))
    key = f"{politician}|{ticker}|{tx_date}|{report_date}|{action}|{low}|{high}"
    return Disclosure(
        id=hashlib.sha1(key.encode()).hexdigest()[:16],
        politician=politician,
        chamber=str(rec.get("House") or rec.get("Chamber") or ""),
        party=str(rec.get("Party") or ""),
        ticker=ticker,
        ticker_type=str(rec.get("TickerType") or "").strip(),
        action=action,
        tx_date=tx_date,
        report_date=report_date,
        amount_low=low,
        amount_high=high,
        description=str(rec.get("Description") or ""),
    )


class QuiverSource:
    def __init__(self, api_key, session=None):
        if not api_key:
            raise SystemExit("QUIVER_API_KEY is not set.")
        self.api_key = api_key
        self.session = session or requests.Session()

    def fetch(self):
        last_err = None
        for attempt in range(3):
            try:
                resp = self.session.get(
                    QUIVER_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                    timeout=60,
                )
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"Quiver rejected the request (HTTP {resp.status_code}). "
                    "Check QUIVER_API_KEY and that your plan is active."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            records = resp.json()
            out = [d for d in (from_record(r) for r in records) if d]
            log.info("Fetched %d records, %d usable", len(records), len(out))
            return out
        raise RuntimeError(f"Could not fetch disclosures: {last_err}")


class CSVSource:
    """Offline source; columns use the same names as the Quiver fields."""

    def __init__(self, path):
        self.path = path

    def fetch(self):
        with open(self.path, newline="") as fh:
            return [d for d in (from_record(r) for r in csv.DictReader(fh)) if d]
