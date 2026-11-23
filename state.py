"""SQLite state: which disclosures were handled, and an audit log of every order."""
import sqlite3
from datetime import datetime, timezone


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                id TEXT PRIMARY KEY, ts TEXT, status TEXT, note TEXT);
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, symbol TEXT,
                side TEXT, notional REAL, broker_order_id TEXT, status TEXT,
                dry_run INTEGER, politicians TEXT);
            """
        )

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def seen_ids(self):
        return {r[0] for r in self.db.execute("SELECT id FROM seen")}

    def mark(self, ids, status, note=""):
        self.db.executemany(
            "INSERT OR REPLACE INTO seen VALUES (?,?,?,?)", [(i, self._now(), status, note) for i in ids]
        )
        self.db.commit()

    def log_order(self, day, symbol, side, notional, broker_order_id, status, dry_run, politicians):
        self.db.execute(
            "INSERT INTO orders (ts, day, symbol, side, notional, broker_order_id, status, dry_run, politicians) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (self._now(), str(day), symbol, side, notional, broker_order_id, status,
             int(dry_run), ", ".join(politicians)),
        )
        self.db.commit()

    def spent_today(self, day, dry_run):
        row = self.db.execute(
            "SELECT COALESCE(SUM(notional), 0) FROM orders WHERE day=? AND side='buy' AND dry_run=?",
            (str(day), int(dry_run)),
        ).fetchone()
        return float(row[0])
