import json
from datetime import datetime

trades = []
with open('trades_log.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            trades.append(json.loads(line))

# Filter today (2026-10-18)
today_trades = [t for t in trades if datetime.fromtimestamp(t.get('ts', 0)).strftime('%Y-%m-%d') == '2026-10-18']

print(f"=== TODAY's TRADES (2026-10-18) ===")
print(f"Total trades today: {len(today_trades)}")

pnl_usd = sum(t.get('pnl_usd', 0) for t in today_trades)
wins = [t for t in today_trades if t.get('pnl_usd', 0) > 0]
losses = [t for t in today_trades if t.get('pnl_usd', 0) < 0]

print(f"Total PnL today: ${pnl_usd:.2f}")
print(f"Wins: {len(wins)}, Losses: {len(losses)}, Win Rate: {len(wins)/len(today_trades)*100:.1f}%")

gross_profit = sum(t.get('pnl_usd', 0) for t in wins)
gross_loss = abs(sum(t.get('pnl_usd', 0) for t in losses))
pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
print(f"Gross Profit: ${gross_profit:.2f}, Gross Loss: -${gross_loss:.2f}, Profit Factor: {pf:.2f}")

by_chain = {}
for t in today_trades:
    c = t.get('chain', 'unknown')
    by_chain.setdefault(c, []).append(t)

print("\n--- By Chain Today ---")
for c, c_trades in by_chain.items():
    c_pnl = sum(t.get('pnl_usd', 0) for t in c_trades)
    c_wins = len([t for t in c_trades if t.get('pnl_usd', 0) > 0])
    c_losses = len([t for t in c_trades if t.get('pnl_usd', 0) < 0])
    c_gp = sum(t.get('pnl_usd', 0) for t in c_trades if t.get('pnl_usd', 0) > 0)
    c_gl = abs(sum(t.get('pnl_usd', 0) for t in c_trades if t.get('pnl_usd', 0) < 0))
    print(f"{c:12}: {len(c_trades):2} trades | {c_wins:2}W / {c_losses:2}L | WR: {c_wins/len(c_trades)*100:4.1f}% | PnL: ${c_pnl:6.2f} (GP: +${c_gp:.2f}, GL: -${c_gl:.2f})")

print("\n--- Categorizing Losses on Robinhood Today ---")
rh_trades = [t for t in today_trades if t.get('chain') == 'robinhood']
for t in rh_trades:
    pnl = t.get('pnl_usd', 0)
    pnl_pct = t.get('pnl_pct', 0)
    sym = t.get('symbol', '???')
    reason = t.get('exit_reason', '')
    peak = t.get('peak_pct', 0)
    dt = datetime.fromtimestamp(t.get('ts', 0)).strftime('%H:%M:%S')
    print(f"[{dt}] {sym:10} | PnL: ${pnl:6.2f} ({pnl_pct:6.1f}%) | Peak: +{peak:5.1f}% | Reason: {reason}")
