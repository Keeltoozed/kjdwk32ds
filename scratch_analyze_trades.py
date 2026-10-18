import json
from datetime import datetime

trades = []
with open('trades_log.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            trades.append(json.loads(line))

print(f"Total trades in log: {len(trades)}")

pnl_usd_total = sum(t.get('pnl_usd', 0) for t in trades)
wins = [t for t in trades if t.get('pnl_usd', 0) > 0]
losses = [t for t in trades if t.get('pnl_usd', 0) < 0]
zeros = [t for t in trades if t.get('pnl_usd', 0) == 0]

print(f"Total PnL: ${pnl_usd_total:.2f}")
print(f"Wins: {len(wins)}, Losses: {len(losses)}, Breakeven: {len(zeros)}")
win_rate = (len(wins) / len(trades) * 100) if trades else 0
print(f"Win Rate: {win_rate:.1f}%")

gross_profit = sum(t.get('pnl_usd', 0) for t in wins)
gross_loss = abs(sum(t.get('pnl_usd', 0) for t in losses))
pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
print(f"Gross Profit: ${gross_profit:.2f}, Gross Loss: -${gross_loss:.2f}, Profit Factor: {pf:.2f}")

by_chain = {}
for t in trades:
    c = t.get('chain', 'unknown')
    by_chain.setdefault(c, []).append(t)

print("\n--- By Chain ---")
for c, c_trades in by_chain.items():
    c_pnl = sum(t.get('pnl_usd', 0) for t in c_trades)
    c_wins = len([t for t in c_trades if t.get('pnl_usd', 0) > 0])
    c_losses = len([t for t in c_trades if t.get('pnl_usd', 0) < 0])
    print(f"{c:12}: {len(c_trades):2} trades | Wins: {c_wins:2} Losses: {c_losses:2} | WinRate: {c_wins/len(c_trades)*100:4.1f}% | PnL: ${c_pnl:6.2f}")

print("\n--- All Trades Details (Chronological) ---")
for i, t in enumerate(trades, 1):
    dt = datetime.fromtimestamp(t.get('ts', 0)).strftime('%Y-%m-%d %H:%M:%S')
    sym = t.get('symbol', '???')
    ch = t.get('chain', '???')
    pnl_usd = t.get('pnl_usd', 0)
    pnl_pct = t.get('pnl_pct', 0)
    peak = t.get('peak_pct', 0)
    reason = t.get('exit_reason', '')
    print(f"{i:2}. [{dt}] {ch:10} {sym:10} | PnL: ${pnl_usd:6.2f} ({pnl_pct:6.1f}%) | Peak: +{peak:5.1f}% | Exit: {reason[:45]}")
