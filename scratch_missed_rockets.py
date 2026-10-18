import sqlite3
import json

conn = sqlite3.connect('shadow_trades.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT mint, symbol, chain, rejected_at, reason, score, rkt_score, m5_pct, price_at_rejection, ath_price, hypothetical_pnl
    FROM rejected_tokens
    WHERE is_missed_rocket = 1 OR hypothetical_pnl >= 50
    ORDER BY hypothetical_pnl DESC
""")
rows = cursor.fetchall()

print(f"Total missed rockets / tokens with ATH >= +50%: {len(rows)}")
print("-" * 100)
for r in rows:
    mint, symbol, chain, rej_at, reason, score, rkt_score, m5_pct, p_rej, p_ath, pnl = r
    print(f"[{rej_at[:19]}] {chain:9} | {symbol:10} | +{pnl:6.1f}% (ATH: {p_ath:.7f}) | M5%: {m5_pct:5.1f}% | RKT: {rkt_score} | Reason: {reason}")

print("\n--- Breakdown of Rejection Reasons for Missed Rockets ---")
reasons = {}
for r in rows:
    reason = r[4]
    # group reason
    key = reason.split()[0] if reason else 'unknown'
    reasons[key] = reasons.get(key, 0) + 1

for k, v in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
    print(f"{k:30}: {v}")
