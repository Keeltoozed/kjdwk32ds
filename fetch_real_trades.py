import os
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
sb = create_client(url, key)

# Fetch all closed trades from trades_pump
print("=== TRADES_PUMP (Closed) ===")
result = sb.table("trades_pump").select("*").eq("status", "CLOSED").execute()
pump_trades = result.data
print(f"Всего закрытых сделок: {len(pump_trades)}")
if pump_trades:
    for t in pump_trades[:3]:
        print(t)

print()
print("=== TRADES_RAYDIUM (Closed) ===")
result2 = sb.table("trades_raydium").select("*").eq("status", "CLOSED").execute()
ray_trades = result2.data
print(f"Всего закрытых сделок: {len(ray_trades)}")
if ray_trades:
    for t in ray_trades[:3]:
        print(t)

print()
print("=== TRADES_PUMP (All statuses) ===")
result3 = sb.table("trades_pump").select("mint, status, pnl, confidence, exit_reason, entry_time, exit_time").execute()
all_pump = result3.data
print(f"Всего записей: {len(all_pump)}")
if all_pump:
    # Stats
    closed = [t for t in all_pump if t.get("status") == "CLOSED" and t.get("pnl") is not None]
    print(f"Из них закрытых с PnL: {len(closed)}")
    if closed:
        pnls = [t["pnl"] for t in closed]
        print(f"Средний PnL: {sum(pnls)/len(pnls):.2f}%")
        print(f"Мин PnL: {min(pnls):.2f}%")
        print(f"Макс PnL: {max(pnls):.2f}%")
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        print(f"Выигрышей: {len(wins)}, Убытков: {len(losses)}")
        print(f"Реальный Win Rate: {len(wins)/len(pnls)*100:.1f}%")
        
        # Distribution of exit reasons
        from collections import Counter
        reasons = Counter([t.get("exit_reason", "unknown") for t in closed])
        print("\nПричины выхода:")
        for reason, count in reasons.most_common():
            avg_pnl = sum(t["pnl"] for t in closed if t.get("exit_reason") == reason) / count
            print(f"  {reason}: {count}x (avg PnL: {avg_pnl:.1f}%)")
