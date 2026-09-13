import os, json
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
sb = create_client(url, key)

# All closed trades from both tables
pump = sb.table("trades_pump").select("mint, pnl, confidence, exit_reason, entry_time, exit_time, features").eq("status", "CLOSED").execute()
ray = sb.table("trades_raydium").select("mint, pnl, confidence, exit_reason, entry_time, exit_time, features").eq("status", "CLOSED").execute()

all_trades = []
for t in pump.data:
    t["source"] = "pump"
    all_trades.append(t)
for t in ray.data:
    t["source"] = "raydium"
    all_trades.append(t)

# Save to local file
with open("real_trades.json", "w") as f:
    json.dump(all_trades, f, indent=2, default=str)

print(f"Saved {len(all_trades)} real trades to real_trades.json")

# Detailed breakdown
pnls = [t["pnl"] for t in all_trades if t.get("pnl") is not None]
print(f"\n=== ПОЛНАЯ СТАТИСТИКА РЕАЛЬНЫХ СДЕЛОК ===")
print(f"Всего сделок: {len(pnls)}")
print(f"Средний PnL: {sum(pnls)/len(pnls):.2f}%")

# Buckets
buckets = {
    "Убыток > -50%": [p for p in pnls if p <= -50],
    "Убыток -50% to -25%": [p for p in pnls if -50 < p <= -25],
    "Убыток -25% to -10%": [p for p in pnls if -25 < p <= -10],
    "Около нуля -10% to +10%": [p for p in pnls if -10 < p <= 10],
    "Профит +10% to +50%": [p for p in pnls if 10 < p <= 50],
    "Большой профит +50% to +100%": [p for p in pnls if 50 < p <= 100],
    "ИКСЫ > +100%": [p for p in pnls if p > 100],
}
print("\nРаспределение PnL:")
for name, bucket in buckets.items():
    print(f"  {name}: {len(bucket)} сделок ({len(bucket)/len(pnls)*100:.1f}%)")
