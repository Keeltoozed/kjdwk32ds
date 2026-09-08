from tracker import PaperTracker
import os
import json

tracker = PaperTracker()
tracker.add_position("TEST", "1234567890", 1.0, 10.0, ml_features={"test": 1}, ml_confidence=99)
tracker.close_position("1234567890", 2.0, "Take Profit (+50%)")

# verify DB
import sqlite3
with sqlite3.connect("trade_journal.db") as conn:
    res = conn.execute("SELECT mint, pnl, status FROM trades WHERE mint='1234567890'").fetchone()
    print("DB Result:", res)
