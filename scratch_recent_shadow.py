import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect('shadow_trades.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT count(*), min(rejected_at), max(rejected_at)
    FROM rejected_tokens
    WHERE rejected_at >= '2026-10-18T18:00:00'
""")
print("Rejected tokens since 18:00:", cursor.fetchone())

cursor.execute("""
    SELECT symbol, chain, rejected_at, reason, hypothetical_pnl, ath_price
    FROM rejected_tokens
    WHERE rejected_at >= '2026-10-18T18:00:00'
    ORDER BY hypothetical_pnl DESC
    LIMIT 20
""")
rows = cursor.fetchall()
print("\nTop tokens since 18:00 in shadow DB:")
for r in rows:
    print(r)
